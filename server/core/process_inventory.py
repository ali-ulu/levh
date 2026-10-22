"""Count live ``mcp stdio`` servers, so doctor can price their memory (#526).

Every ``levh mcp stdio`` process builds its own engine through
``engine_provider``, and with a local embedder that means its own copy of torch
plus ``all-MiniLM-L6-v2`` — roughly 600 MB resident, 974 MB private after the
first embed, measured on the machine that filed the issue. A handful of agents
each keeping a stdio session open pushes an 8 GB box past its commit limit; the
2026-09-30 outage on that machine had exactly this cause.

The count has to come from the OS, not from LEVH's own bookkeeping. A stdio
process that has not yet called a tool has no session row and no heartbeat, so
``agent_sessions`` cannot see the ones that have not paid the cost *yet* — and
those are precisely the ones about to. ``levh doctor`` runs on the same machine,
so it can look.

Enumeration is best-effort by design: this is a warning, and a platform that
cannot be read (a locked-down ``/proc``, a missing ``ps``, a PowerShell that
refuses to start) returns ``None`` rather than a wrong number.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

#: Warn once this many stdio servers hold a local model. Three copies is
#: already ~1.8 GB of private RSS before the base interpreter and the store;
#: a 64 GB workstation can raise it. Override with
#: ``LEVH_DOCTOR_STDIO_WARN_THRESHOLD``.
DEFAULT_STDIO_WARN_THRESHOLD = 3

#: Env override for the threshold above, read by ``levh doctor``.
STDIO_WARN_THRESHOLD_ENV = "LEVH_DOCTOR_STDIO_WARN_THRESHOLD"

#: Per-process resident cost of a loaded local model, from the issue's
#: measurement (974 MB private, ~600 MB attributable to the model). Reported as
#: an estimate, never as a computed total that implies precision we do not have.
ESTIMATED_MB_PER_STDIO_MODEL = 600

#: How long to wait for a platform process listing. A doctor check that hangs
#: on ``ps`` is worse than one that says it could not tell.
_LISTING_TIMEOUT_SECONDS = 10.0

#: ``levh mcp stdio`` launches the module entry point; a hand-written config may
#: spell it as a path. Three shapes count, and each needs its own marker so an
#: unrelated ``mcp stdio`` word pair — a ``grep -r mcp stdio`` in the process
#: list — is not miscounted as a server:
#:   * the module entry point (``-m server.mcp_stdio``), marker ``mcp_stdio``;
#:   * the CLI module (``-m server.cli mcp stdio``), marker ``server.cli``;
#:   * the console script (``levh mcp stdio``), marker ``levh``.
_MODULE_MARKERS = ("server.mcp_stdio", "server/mcp_stdio", "mcp_stdio.py")
_CLI_MODULE_RE = re.compile(r"\bserver[./]cli\b")
_CLI_SCRIPT_RE = re.compile(r"\blevh(\.exe)?\b")
_CLI_FORM_RE = re.compile(r"(^|\s)mcp\s+stdio(\s|$)")


def looks_like_mcp_stdio(command: str) -> bool:
    """Whether one process command line is an ``mcp stdio`` server.

    Takes the whole command as a string, not argv: the POSIX fallback (``ps``)
    and PowerShell both hand back a single line, and re-splitting it would
    mangle a quoted interpreter path — the exact bug ``paths.py`` documents for
    Windows ``C:\\Users\\...`` arguments.
    """
    text = command.replace("\\", "/")
    if any(marker in text for marker in _MODULE_MARKERS):
        return True
    if not _CLI_FORM_RE.search(text):
        return False
    return _CLI_MODULE_RE.search(text) is not None or _CLI_SCRIPT_RE.search(text) is not None


def count_mcp_stdio_processes() -> int | None:
    """Live ``mcp stdio`` processes, or ``None`` if the platform cannot be read.

    ``None`` is a distinct answer from ``0``: the caller must not report "no
    stdio servers" when it merely failed to look.
    """
    commands = _command_lines()
    if commands is None:
        return None
    return sum(1 for command in commands if looks_like_mcp_stdio(command))


def describe_stdio_load(count: int, *, per_process_mb: int = ESTIMATED_MB_PER_STDIO_MODEL) -> str:
    """The warning body: what was seen, what it costs, and the levers.

    Deliberately does not prescribe a shared-server configuration: sharing the
    embedder across stdio processes (the issue's proposal 1) is not built yet,
    and recommending a half-configuration — local in ``serve``, hash in the
    stdio clients — would mix two vector spaces in one store. The honest
    options are fewer sessions, or hash everywhere, and the issue reference
    says which work would change that.
    """
    return (
        f"{count} `mcp stdio` process(es) with a local embedder; each loads its own "
        f"copy of the model (~{per_process_mb} MB resident, measured). Close idle "
        f"sessions, or set EMBEDDER_MODE=hash for the whole store if the memory "
        f"matters more than semantic recall (see #526 for sharing one model)"
    )


# ── Platform enumeration ─────────────────────────────────────────────


def _command_lines() -> list[str] | None:
    """Command lines of every process we can read, or ``None`` if we cannot."""
    if sys.platform.startswith("linux"):
        lines = _linux_command_lines()
        if lines is not None:
            return lines
    if sys.platform == "win32":
        return _windows_command_lines()
    return _posix_command_lines()


def _linux_command_lines(proc_root: str = "/proc") -> list[str] | None:
    """Read ``<proc_root>/<pid>/cmdline``; ``None`` when ``/proc`` is absent.

    Each file is NUL-separated argv, so joining on spaces rebuilds the command
    line without the shell quoting ``ps`` would add. ``proc_root`` is a
    parameter only so a test can point at a fixture tree.
    """
    try:
        pids = [name for name in os.listdir(proc_root) if name.isdigit()]
    except OSError:
        return None
    lines: list[str] = []
    for pid in pids:
        try:
            with open(os.path.join(proc_root, pid, "cmdline"), "rb") as handle:
                raw = handle.read()
        except OSError:
            # A process that exited between listdir and open, or one we are not
            # allowed to read. Neither is a reason to abandon the count.
            continue
        if not raw:
            continue
        parts = [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]
        if parts:
            lines.append(" ".join(parts))
    return lines


def _posix_command_lines() -> list[str] | None:
    """``ps -Ao command=`` — macOS, the BSDs, and a Linux without ``/proc``."""
    return _run_listing(["ps", "-Ao", "command="])


def _windows_command_lines() -> list[str] | None:
    """PowerShell's CIM view of every process command line.

    ``wmic`` was the old way and is removed on current Windows builds, so CIM is
    the supported path. ``tasklist`` is not an option: it does not print the
    command line, which is the only thing that identifies the transport.
    """
    return _run_listing(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Get-CimInstance Win32_Process | Select-Object -ExpandProperty CommandLine",
        ]
    )


def _run_listing(cmd: list[str]) -> list[str] | None:
    """Run a process listing, returning its non-empty lines or ``None``."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_LISTING_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]
