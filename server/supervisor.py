"""Keep ``levh serve`` running, and optionally keep it on the newest code.

    cd <checkout> && python -m server.supervisor

A plain process supervisor for machines that do not want Docker or a system
service: it starts ``python -m server.cli serve`` as its child and restarts it
when it dies. Launch it from whatever runs at logon (on Windows a Scheduled
Task firing every few minutes - a lock file makes extra launches exit at once).

Rules, each learned from a real outage:

* Cold boot takes 40-109 s on a RAM-starved machine, so a young child is never
  judged, and a slow answer gets ten minutes of patience.
* Only the supervisor's own child is ever killed. A server already listening on
  the port (started by hand) is adopted and left alone.
* A port that actively *refuses* while the child is alive means the listener is
  gone (the Windows proactor accept bug, see server/core/event_loop.py), which
  is proof rather than slowness: that case restarts within two probes.
* A flapping server (a few good probes between runs of bad ones) is as
  unusable as a dead one, so one good probe does not wipe the evidence: the
  child is restarted once ``flap_miss_threshold`` of the last
  ``flap_window_probes`` probes were missed, however they are interleaved.

Auto-update: every ``LEVH_AUTO_UPDATE_HOURS`` hours (default 6, ``0`` turns it
off; the first check runs 15 minutes after start) the supervisor asks
``levh update --check``. Only when the checkout is behind does it stop the
server, run ``levh update`` - which fast-forwards, reinstalls dependencies when
packaging files changed and rolls back if that install fails - and start the
server again on whatever code is then on disk. Stopping first matters on
Windows, where an installer cannot replace DLLs a running server holds open.
The update command refuses dirty trees, other branches and diverged
histories, so the timer cannot destroy local work. The supervisor's own code is
reloaded the next time it is launched.
"""

from __future__ import annotations

import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import IO, Callable

REPO = Path(__file__).resolve().parents[1]
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
LOG_MAX_BYTES = 1_000_000
SERVE_LOG_MAX_BYTES = 5_000_000
CHECK_TIMEOUT_S = 300
UPDATE_TIMEOUT_S = 1200
GRACEFUL_STOP_S = 15
DEFAULT_UPDATE_HOURS = 6.0
FIRST_UPDATE_DELAY_S = 900
# Not a bind: probe_host() maps these to loopback for the health probe.
WILDCARD_HOSTS = {"0.0.0.0", "::", ""}  # nosec B104


def _default_log_dir() -> Path:
    override = os.environ.get("LEVH_SUPERVISOR_LOG_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "LevH"
    return Path.home() / ".local" / "state" / "levh"


def _update_hours_from_env() -> float:
    raw = os.environ.get("LEVH_AUTO_UPDATE_HOURS", "").strip()
    if not raw:
        return DEFAULT_UPDATE_HOURS
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_UPDATE_HOURS


@dataclass(frozen=True)
class SupervisorConfig:
    host: str = "127.0.0.1"
    port: int = 8000
    log_dir: Path = field(default_factory=_default_log_dir)
    poll_s: float = 60.0              # probe interval while our child runs
    probe_timeout_s: float = 20.0     # RAM thrash makes even /api/health slow
    boot_grace_s: float = 600.0       # no health judgement for the first 10 minutes
    max_failed_probes: int = 10       # consecutive slow/failed probes before a restart
    flap_window_probes: int = 10      # recent probes remembered for the flapping rule
    flap_miss_threshold: int = 6      # misses within that window = flapping, restart
    refused_grace_s: float = 180.0    # boot window in which a closed port is expected
    max_refused_probes: int = 2       # refused twice = the listener is gone
    adopt_poll_s: float = 30.0        # recheck interval while someone else holds the port
    quick_exit_s: float = 60.0        # dying faster than this counts as a crash loop
    backoff_start_s: float = 5.0
    backoff_max_s: float = 300.0
    update_interval_s: float = field(default_factory=lambda: _update_hours_from_env() * 3600)
    first_update_delay_s: float = FIRST_UPDATE_DELAY_S


def next_backoff(previous: float, uptime: float, cfg: SupervisorConfig) -> float:
    """Double the delay for a crash loop, reset it after a run that lasted."""
    if uptime < cfg.quick_exit_s:
        return min(max(previous, cfg.backoff_start_s) * 2, cfg.backoff_max_s)
    return cfg.backoff_start_s


def probe_host(host: str) -> str:
    """Where to connect for a probe: a wildcard bind is reachable on loopback."""
    return "127.0.0.1" if host in WILDCARD_HOSTS else host


def server_python(executable: str = sys.executable) -> str:
    """The console interpreter next to a ``pythonw.exe`` supervisor, if any."""
    path = Path(executable)
    if path.name.lower() == "pythonw.exe":
        sibling = path.with_name("python.exe")
        if sibling.exists():
            return str(sibling)
    return executable


def child_command(cfg: SupervisorConfig, python: str | None = None) -> list[str]:
    return [python or server_python(), "-m", "server.cli", "serve",
            "--host", cfg.host, "--port", str(cfg.port)]


def parse_update(stdout: str) -> dict[str, object]:
    """The JSON object ``levh update --json`` printed last, or an ``unreadable`` stub."""
    try:
        value = json.loads(stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"status": "unreadable", "reason": stdout.strip()[-200:]}
    return value if isinstance(value, dict) else {"status": "unreadable", "reason": repr(value)}


def describe_update(result: dict[str, object]) -> str:
    old, new = str(result.get("old_head", ""))[:8], str(result.get("new_head", ""))[:8]
    text = f"update status={result.get('status')} {old}->{new}"
    return text + (f" reason={result['reason']}" if result.get("reason") else "")


def _no_proxy_opener() -> urllib.request.OpenerDirector:
    # A system or env proxy must never sit between us and 127.0.0.1.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Supervisor:
    def __init__(
        self,
        cfg: SupervisorConfig,
        *,
        command: list[str] | None = None,
        run_update: Callable[[list[str], float], str] | None = None,
    ) -> None:
        self.cfg = cfg
        self.command = command or child_command(cfg)
        self.run_update = run_update or self._run_update_command
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        self.sup_log = cfg.log_dir / "supervisor.log"
        self.out_log = cfg.log_dir / "serve.out.log"
        self.err_log = cfg.log_dir / "serve.err.log"
        self.lock_path = cfg.log_dir / "supervisor.lock"
        self._lock: IO[str] | None = None
        self._opener = _no_proxy_opener()
        first_due = min(cfg.update_interval_s, cfg.first_update_delay_s)
        self._next_update = time.monotonic() + first_due
        self._failed_target: object = None

    # -- plumbing ----------------------------------------------------------

    @staticmethod
    def _rotate(path: Path, max_bytes: int) -> None:
        try:
            if path.stat().st_size > max_bytes:
                os.replace(path, path.with_name(path.name + ".1"))
        except OSError:
            pass

    def log(self, message: str) -> None:
        self._rotate(self.sup_log, LOG_MAX_BYTES)
        stamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        with open(self.sup_log, "a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {message}\n")

    def acquire_lock(self) -> bool:
        handle = open(self.lock_path, "a+", encoding="utf-8")
        try:
            # Literal sys.platform checks so mypy skips the other OS's branch.
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._lock = handle  # held open for the life of the process
        return True

    def release_lock(self) -> None:
        if self._lock is not None:
            self._lock.close()
            self._lock = None

    def _connect(self, timeout: float) -> str:
        """'open', 'refused' or 'unknown' (a timeout under load proves nothing)."""
        try:
            with socket.create_connection((probe_host(self.cfg.host), self.cfg.port), timeout=timeout):
                return "open"
        except ConnectionRefusedError:
            return "refused"
        except OSError:
            return "unknown"

    def port_open(self) -> bool:
        return self._connect(3) == "open"

    def port_refused(self) -> bool:
        return self._connect(10) == "refused"

    def healthy(self) -> bool:
        url = f"http://{probe_host(self.cfg.host)}:{self.cfg.port}/api/health"
        try:
            with self._opener.open(url, timeout=self.cfg.probe_timeout_s) as response:
                return bool(response.status == 200)
        except (OSError, http.client.HTTPException, ValueError):
            # URLError/HTTPError/timeouts are OSError; a garbled reply is HTTPException.
            return False

    def _run_update_command(self, extra: list[str], timeout: float) -> str:
        from server.commands.update import run_bounded

        out = run_bounded([server_python(), "-m", "server.cli", "update", "--json", *extra],
                          cwd=REPO, timeout=timeout)
        if out.returncode != 0 and out.stderr.strip():
            self.log(f"update rc={out.returncode} stderr: {out.stderr.strip()[-300:]}")
        return out.stdout

    # -- child lifecycle ---------------------------------------------------

    def start_child(self) -> subprocess.Popen[bytes]:
        self._rotate(self.out_log, SERVE_LOG_MAX_BYTES)
        self._rotate(self.err_log, SERVE_LOG_MAX_BYTES)
        with open(self.out_log, "ab") as out, open(self.err_log, "ab") as err:
            child = subprocess.Popen(
                self.command, cwd=REPO, stdout=out, stderr=err,
                stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW,
                start_new_session=sys.platform != "win32",
            )
        self.log(f"started child pid={child.pid}")
        return child

    def stop_child(self, child: subprocess.Popen[bytes], *, graceful: bool = False) -> None:
        """Kill the child's whole tree; POSIX gets a SIGTERM window first when graceful.

        Windows has no console-less graceful stop for it; SQLite's WAL makes the
        hard kill safe for the store.
        """
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(child.pid)],
                           capture_output=True, creationflags=CREATE_NO_WINDOW)
        else:
            self._signal_group(child, signal.SIGTERM if graceful else signal.SIGKILL)
            if graceful:
                try:
                    child.wait(timeout=GRACEFUL_STOP_S)
                except subprocess.TimeoutExpired:
                    self._signal_group(child, signal.SIGKILL)
        try:
            child.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.log(f"child pid={child.pid} did not exit 30s after the kill")

    @staticmethod
    def _signal_group(child: subprocess.Popen[bytes], sig: int) -> None:
        if sys.platform != "win32":
            try:
                os.killpg(child.pid, sig)
            except ProcessLookupError:
                pass

    # -- auto-update -------------------------------------------------------

    def update_due(self) -> bool:
        return self.cfg.update_interval_s > 0 and time.monotonic() >= self._next_update

    def _call_update(self, extra: list[str], timeout: float) -> dict[str, object]:
        try:
            result = parse_update(self.run_update(extra, timeout))
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            result = {"status": "error", "reason": f"update could not run: {exc!r}"}
        self.log(describe_update(result))
        return result

    def update_cycle(self, child: subprocess.Popen[bytes] | None) -> bool:
        """When due and behind: stop ``child``, update. True when ``child`` was stopped."""
        if not self.update_due():
            return False
        self._next_update = time.monotonic() + self.cfg.update_interval_s
        check = self._call_update(["--check"], CHECK_TIMEOUT_S)
        if check.get("status") != "behind":
            return False
        target = check.get("new_head")
        if target and target == self._failed_target:
            # Same upstream commit whose install already failed and was rolled
            # back: retrying would only cost another outage. Wait for a new push.
            self.log(f"update to {str(target)[:8]} failed before - waiting for a newer upstream commit")
            return False
        if child is not None:
            self.log(f"child pid={child.pid} stopping for the update")
            self.stop_child(child, graceful=True)
        result = self._call_update([], UPDATE_TIMEOUT_S)
        self._failed_target = target if result.get("deps_synced") is False else None
        return child is not None

    # -- supervision -------------------------------------------------------

    def watch_child(self, child: subprocess.Popen[bytes]) -> float:
        """Block until the child exits or must be replaced. Returns its uptime."""
        cfg = self.cfg
        started = time.monotonic()
        misses = refused = 0
        recent: deque[bool] = deque(maxlen=cfg.flap_window_probes)  # True = missed
        while True:
            try:
                child.wait(timeout=cfg.poll_s)
                uptime = time.monotonic() - started
                self.log(f"child pid={child.pid} EXITED rc={child.returncode} uptime={uptime:.0f}s")
                return uptime
            except subprocess.TimeoutExpired:
                pass
            age = time.monotonic() - started
            if self.update_cycle(child):
                return age
            if age >= cfg.refused_grace_s and self.port_refused():
                refused += 1
                self.log(f"child pid={child.pid} alive but port {cfg.port} refused "
                         f"({refused}/{cfg.max_refused_probes})")
                if refused >= cfg.max_refused_probes:
                    self.log(f"child pid={child.pid} lost its listener - restarting it")
                    self.stop_child(child)
                    return age
                continue
            refused = 0
            if age < cfg.boot_grace_s:
                continue
            if self.healthy():
                recent.append(False)
                if misses:
                    self.log(f"child pid={child.pid} healthy again after {misses} missed probe(s)")
                misses = 0
                continue
            misses += 1
            recent.append(True)
            self.log(f"child pid={child.pid} health probe missed ({misses}/{cfg.max_failed_probes})")
            if sum(recent) >= cfg.flap_miss_threshold and misses < cfg.max_failed_probes:
                self.log(f"child pid={child.pid} flapping: {sum(recent)} of the last "
                         f"{len(recent)} probes missed - restarting it")
                self.stop_child(child)
                return time.monotonic() - started
            if misses >= cfg.max_failed_probes:
                self.log(f"child pid={child.pid} unresponsive - restarting it")
                self.stop_child(child)
                return time.monotonic() - started

    def supervise_once(self, backoff: float) -> float:
        """One round: adopt, or start and watch a child. Returns the next backoff."""
        if self.port_open():
            time.sleep(self.cfg.adopt_poll_s)
            return backoff
        # No child is running here, so an update (if due) needs no stop.
        self.update_cycle(None)
        try:
            child = self.start_child()
        except OSError as exc:
            self.log(f"FAILED to start child: {exc!r}")
            return self.cfg.backoff_max_s
        backoff = next_backoff(backoff, self.watch_child(child), self.cfg)
        self.log(f"restarting in {backoff:g}s")
        time.sleep(backoff)
        return backoff

    def run(self, *, rounds: int | None = None) -> int:
        if self._lock is None and not self.acquire_lock():
            return 0  # another supervisor already owns this server
        self.log(f"supervisor up pid={os.getpid()} repo={REPO} "
                 f"auto_update_h={self.cfg.update_interval_s / 3600:g}")
        backoff = self.cfg.backoff_start_s
        adopted = False
        done = 0
        while rounds is None or done < rounds:
            holding = self.port_open()
            if holding and not adopted:
                self.log(f"port {self.cfg.port} already served by another process - adopting")
            adopted = holding
            backoff = self.supervise_once(backoff)
            done += 1
        return 0


def main() -> int:
    # Take the lock before importing anything heavy: on a busy machine the
    # every-5-minutes launch must cost next to nothing when a supervisor runs.
    bootstrap = Supervisor(SupervisorConfig())
    if not bootstrap.acquire_lock():
        return 0
    try:
        from server.core.runtime_config import resolve_runtime_config

        runtime = resolve_runtime_config(cwd=str(REPO))
        supervisor = Supervisor(SupervisorConfig(host=runtime.api_host, port=runtime.api_port))
        supervisor._lock, bootstrap._lock = bootstrap._lock, None
        return supervisor.run()
    except Exception as exc:  # leave a trace; the logon task's next tick revives us
        bootstrap.log(f"supervisor CRASHED: {exc!r}")
        raise


if __name__ == "__main__":
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    sys.exit(main())
