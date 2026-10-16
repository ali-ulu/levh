"""``levh update``: keep a git checkout on the tip of its upstream branch.

A slice of the ``levh`` CLI. The parsers and the dispatch chain stay in
server/cli.py; this module holds the implementation.

The command only ever fast-forwards. Anything that would need a decision -
local edits to tracked files, a different branch checked out, a history that
is not a strict ancestor of upstream - is reported and left untouched, so an
unattended caller (``server/supervisor.py``) can run it on a timer without
being able to destroy work. Untracked files (logs, databases) never block it.

When ``pyproject.toml`` or ``uv.lock`` changed, the dependencies are
reinstalled; if that fails the checkout is moved back to where it was
(``git reset --keep``), so the tree on disk never holds code whose
dependencies are missing. Every git and installer call is non-interactive and
time-boxed: a credential prompt or a hung network call becomes an error, not a
frozen process.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Literal, Sequence

UpdateStatus = Literal["up_to_date", "updated", "behind", "skipped", "error"]

# A change to either file can change what must be installed for the new code.
PACKAGING_FILES = ("pyproject.toml", "uv.lock")
GIT_TIMEOUT_S = 120
INSTALL_TIMEOUT_S = 900
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# Never let git or ssh wait for a human: an unattended run must fail fast.
NON_INTERACTIVE_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GCM_INTERACTIVE": "never",
    "GIT_ASKPASS": "",
    "SSH_ASKPASS": "",
    # Stable English messages: the "not a git repository" check reads stderr.
    "LC_ALL": "C",
}
# A ref of our own: FETCH_HEAD is shared, so a user's concurrent `git fetch`
# could otherwise swap the commit we are about to fast-forward to.
TARGET_REF = "refs/levh/update-target"


@dataclass(frozen=True)
class UpdateResult:
    status: UpdateStatus
    old_head: str
    new_head: str
    reason: str = ""
    behind: int = 0
    # None: packaging files unchanged, nothing to do. True/False: sync outcome.
    deps_synced: bool | None = None


class CommandError(RuntimeError):
    pass


@dataclass(frozen=True)
class _Output:
    returncode: int
    stdout: str
    stderr: str


def _kill_tree(proc: subprocess.Popen[str]) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       capture_output=True, creationflags=CREATE_NO_WINDOW)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run_bounded(cmd: Sequence[str], *, cwd: Path, timeout: float,
                env: dict[str, str] | None = None) -> _Output:
    """Run ``cmd`` with no stdin and a hard deadline that kills its whole tree.

    ``subprocess.run(timeout=)`` kills only the direct child; on Windows a
    surviving grandchild (git-remote-https, a credential helper) keeps the pipes
    open and ``communicate()`` then blocks forever.
    """
    proc = subprocess.Popen(
        list(cmd), cwd=cwd, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
        creationflags=CREATE_NO_WINDOW, start_new_session=sys.platform != "win32",
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            pass  # a grandchild outside the tree still holds the pipes; give up on them
        raise CommandError(f"{cmd[0]} {cmd[1] if len(cmd) > 1 else ''} timed out after {timeout:g}s")
    return _Output(proc.returncode, stdout, stderr)


def _git_env() -> dict[str, str]:
    env = {**os.environ, **NON_INTERACTIVE_ENV}
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    return env


def _last_line(text: str) -> str:
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""


def git(repo: Path, *args: str, check: bool = True) -> _Output:
    try:
        out = run_bounded(["git", "-c", "credential.interactive=never", *args],
                          cwd=repo, timeout=GIT_TIMEOUT_S, env=_git_env())
    except FileNotFoundError as exc:
        raise CommandError("git is not installed or not on PATH") from exc
    if check and out.returncode != 0:
        raise CommandError(f"git {args[0]} failed: {_last_line(out.stderr or out.stdout) or out.returncode}")
    return out


def _installer_command(repo: Path) -> list[str] | None:
    """pip when the interpreter has it, else uv's pip (uv venvs ship no pip)."""
    if importlib.util.find_spec("pip") is not None:
        return [sys.executable, "-m", "pip", "install", "--quiet", "-e", str(repo)]
    uv = shutil.which("uv")
    if uv:
        return [uv, "pip", "install", "--quiet", "--python", sys.executable, "-e", str(repo)]
    return None


def install_editable(repo: Path) -> tuple[bool, str]:
    """Reinstall the checkout into the running interpreter: (ok, detail)."""
    cmd = _installer_command(repo)
    if cmd is None:
        return False, "neither pip nor uv is available to install dependencies"
    try:
        out = run_bounded(cmd, cwd=repo, timeout=INSTALL_TIMEOUT_S)
    except (OSError, CommandError) as exc:
        return False, str(exc)
    if out.returncode != 0:
        return False, f"install failed: {_last_line(out.stderr or out.stdout)}"
    return True, ""


def repo_root() -> Path:
    """The checkout this code runs from (``server/commands/update.py`` -> root)."""
    return Path(__file__).resolve().parents[2]


def _precheck(repo: Path, branch: str) -> UpdateResult | str:
    """The current HEAD when the checkout may be updated, else the verdict."""
    probe = git(repo, "rev-parse", "--show-toplevel", check=False)
    if probe.returncode != 0:
        detail = _last_line(probe.stderr)
        if "not a git repository" in detail:
            return UpdateResult("skipped", "", "", "not a git checkout (a wheel install updates with "
                                "`pip install -U levh`)")
        return UpdateResult("error", "", "", f"git cannot read the checkout: {detail}")
    # Rooted at ``repo`` itself: a wheel install that happens to sit inside some
    # unrelated repository (a home directory under version control) must not
    # "update" that repository.
    if Path(probe.stdout.strip()).resolve() != repo.resolve():
        return UpdateResult("skipped", "", "", "not a git checkout root (a wheel install updates with "
                            "`pip install -U levh`)")
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    current = git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if current != branch:
        return UpdateResult("skipped", head, head, f"checkout is on '{current}', auto-update only "
                            f"follows '{branch}'")
    if git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        return UpdateResult("skipped", head, head, "local changes to tracked files; commit or discard them first")
    return head


def _fetch_target(repo: Path, remote: str, branch: str, head: str) -> UpdateResult | str:
    """The fetched upstream commit when HEAD can fast-forward to it, else the verdict."""
    # ``--`` keeps a remote name from being read as an option; the private ref
    # is exact even when the remote's refspec does not track ``branch``.
    git(repo, "fetch", "--quiet", "--no-tags", "--", remote, f"+{branch}:{TARGET_REF}")
    target = git(repo, "rev-parse", TARGET_REF).stdout.strip()
    if target == head:
        return UpdateResult("up_to_date", head, head)
    if git(repo, "merge-base", "--is-ancestor", head, target, check=False).returncode != 0:
        ahead = git(repo, "merge-base", "--is-ancestor", target, head, check=False).returncode == 0
        why = "has local commits not on" if ahead else "diverged from"
        return UpdateResult("skipped", head, head, f"local history {why} {remote}/{branch}; "
                            "not merging unattended")
    return target


def _apply(repo: Path, head: str, target: str,
           sync_deps: Callable[[Path], tuple[bool, str]]) -> UpdateResult:
    behind = int(git(repo, "rev-list", "--count", f"{head}..{target}").stdout.strip())
    changed = git(repo, "diff", "--name-only", head, target).stdout.splitlines()
    git(repo, "merge", "--ff-only", "--quiet", target)
    if not any(name in PACKAGING_FILES for name in changed):
        return UpdateResult("updated", head, target, behind=behind)
    ok, detail = sync_deps(repo)
    if ok:
        return UpdateResult("updated", head, target, behind=behind, deps_synced=True)
    # Never leave code on disk whose dependencies are missing: the next restart
    # (crash, reboot) would boot straight into an ImportError loop.
    rollback = git(repo, "reset", "--keep", head, check=False)
    where = "rolled back" if rollback.returncode == 0 else "ROLLBACK FAILED, checkout left"
    return UpdateResult("error", head, head if rollback.returncode == 0 else target,
                        f"dependency install failed ({detail}); {where} at {head[:8]}",
                        behind=behind, deps_synced=False)


def update_checkout(
    repo: Path,
    *,
    remote: str = "origin",
    branch: str = "main",
    check_only: bool = False,
    sync_deps: Callable[[Path], tuple[bool, str]] = install_editable,
) -> UpdateResult:
    """Fast-forward ``repo`` to ``remote/branch`` when that is provably safe."""
    head = ""
    try:
        checked = _precheck(repo, branch)
        if isinstance(checked, UpdateResult):
            return checked
        head = checked
        fetched = _fetch_target(repo, remote, branch, head)
        if isinstance(fetched, UpdateResult):
            return fetched
        if check_only:
            behind = int(git(repo, "rev-list", "--count", f"{head}..{fetched}").stdout.strip())
            return UpdateResult("behind", head, fetched, behind=behind)
        return _apply(repo, head, fetched, sync_deps)
    except (CommandError, OSError, ValueError) as exc:
        return UpdateResult("error", head, head, str(exc))


def _describe(result: UpdateResult) -> str:
    short_old, short_new = result.old_head[:8], result.new_head[:8]
    if result.status == "updated":
        lines = [f"  Updated {short_old} -> {short_new} ({result.behind} commit(s))."]
        if result.deps_synced:
            lines.append("  Dependencies reinstalled (packaging files changed).")
        lines.append("  Restart `levh serve` to run the new code.")
        return "\n".join(lines)
    if result.status == "behind":
        return f"  {result.behind} commit(s) behind ({short_old} -> {short_new}). Run `levh update`."
    if result.status == "up_to_date":
        return f"  Already up to date ({short_old})."
    label = "Skipped" if result.status == "skipped" else "ERROR"
    return f"  {label}: {result.reason}"


def cmd_update(args: argparse.Namespace) -> int:
    """Fast-forward this checkout; exit 1 when it needs a human's attention."""
    result = update_checkout(
        repo_root(),
        remote=args.remote,
        branch=args.branch,
        check_only=args.check,
    )
    if args.json:
        print(json.dumps(asdict(result)))
    else:
        print(_describe(result))
    return 1 if result.status == "error" else 0
