"""``server/supervisor.py``: restart rules and the auto-update hook.

The child processes here are real (a sleeping interpreter), so the kill and
wait paths run for real; the port probes and the update runner are always
faked, so no test ever touches a server a developer has running on 8000.
"""

from __future__ import annotations

import http.server
import json
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from server import supervisor as sup
from server.supervisor import (
    Supervisor,
    SupervisorConfig,
    child_command,
    describe_update,
    next_backoff,
    parse_update,
    probe_host,
    server_python,
)

SLEEPER = [sys.executable, "-c", "import time; time.sleep(60)"]
QUICK_EXIT = [sys.executable, "-c", "raise SystemExit(3)"]
HEADS = {"old_head": "a" * 40, "new_head": "b" * 40}


@pytest.fixture
def cfg(tmp_path: Path) -> SupervisorConfig:
    return SupervisorConfig(
        log_dir=tmp_path,
        poll_s=0.05,
        boot_grace_s=0,
        refused_grace_s=0,
        update_interval_s=0,
        first_update_delay_s=0,
    )


@pytest.fixture
def make(cfg, monkeypatch):
    """Build supervisors with faked probes; kill every child they start."""
    children: list[subprocess.Popen[bytes]] = []

    def build(config: SupervisorConfig | None = None, *, refused=False, healthy=True,
              port_open=False, **kwargs) -> Supervisor:
        supervisor = Supervisor(config or cfg, **kwargs)
        monkeypatch.setattr(supervisor, "port_refused", lambda: refused)
        monkeypatch.setattr(supervisor, "healthy", lambda: healthy)
        monkeypatch.setattr(supervisor, "port_open", lambda: port_open)
        original = supervisor.start_child

        def tracked():
            child = original()
            children.append(child)
            return child

        monkeypatch.setattr(supervisor, "start_child", tracked)
        return supervisor

    yield build
    for child in children:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=30)


def _log(cfg: SupervisorConfig) -> str:
    return (cfg.log_dir / "supervisor.log").read_text(encoding="utf-8")


def _update_runner(check_status: str, update_payload: dict | None = None):
    calls: list[list[str]] = []

    def run(extra: list[str], timeout: float) -> str:
        calls.append(extra)
        if extra == ["--check"]:
            return json.dumps({"status": check_status, **HEADS})
        return json.dumps(update_payload or {"status": "updated", **HEADS})

    return run, calls


# -- pure rules ---------------------------------------------------------------


def test_backoff_doubles_for_a_crash_loop_and_caps():
    cfg = SupervisorConfig(backoff_start_s=5, backoff_max_s=30, quick_exit_s=60)
    assert next_backoff(5, uptime=1, cfg=cfg) == 10
    assert next_backoff(20, uptime=1, cfg=cfg) == 30
    assert next_backoff(30, uptime=1, cfg=cfg) == 30


def test_backoff_resets_after_a_run_that_lasted():
    cfg = SupervisorConfig(backoff_start_s=5, quick_exit_s=60)
    assert next_backoff(300, uptime=3600, cfg=cfg) == 5


def test_child_command_serves_the_configured_bind():
    cfg = SupervisorConfig(host="127.0.0.1", port=8123)
    assert child_command(cfg, python="py") == [
        "py", "-m", "server.cli", "serve", "--host", "127.0.0.1", "--port", "8123",
    ]


@pytest.mark.parametrize(("bind", "probe"), [("0.0.0.0", "127.0.0.1"), ("::", "127.0.0.1"),
                                              ("127.0.0.1", "127.0.0.1"), ("10.0.0.5", "10.0.0.5")])
def test_wildcard_binds_are_probed_on_loopback(bind, probe):
    assert probe_host(bind) == probe


def test_server_python_swaps_pythonw_for_its_console_sibling(tmp_path):
    (tmp_path / "python.exe").write_text("", encoding="utf-8")
    assert server_python(str(tmp_path / "pythonw.exe")) == str(tmp_path / "python.exe")
    assert server_python(str(tmp_path / "python3")) == str(tmp_path / "python3")


def test_server_python_keeps_pythonw_without_a_sibling(tmp_path):
    assert server_python(str(tmp_path / "pythonw.exe")) == str(tmp_path / "pythonw.exe")


def test_parse_update_reads_the_last_json_line():
    assert parse_update("noise\n" + json.dumps({"status": "behind"}))["status"] == "behind"
    assert parse_update("Traceback ...")["status"] == "unreadable"
    assert parse_update("")["status"] == "unreadable"
    assert parse_update("[1, 2]")["status"] == "unreadable"


def test_describe_update_names_status_heads_and_reason():
    text = describe_update({"status": "skipped", "reason": "dirty", **HEADS})
    assert text == "update status=skipped aaaaaaaa->bbbbbbbb reason=dirty"


def test_update_interval_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("LEVH_AUTO_UPDATE_HOURS", "0")
    assert SupervisorConfig().update_interval_s == 0
    monkeypatch.setenv("LEVH_AUTO_UPDATE_HOURS", "1.5")
    assert SupervisorConfig().update_interval_s == 5400
    monkeypatch.setenv("LEVH_AUTO_UPDATE_HOURS", "soon")
    assert SupervisorConfig().update_interval_s == sup.DEFAULT_UPDATE_HOURS * 3600


def test_health_probe_bypasses_proxies(cfg, monkeypatch):
    class Health(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if self.path == "/api/health" else 404)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Health)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # A dead proxy: any probe routed through it would fail.
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
        monkeypatch.setenv("NO_PROXY", "")
        config = replace(cfg, port=server.server_address[1], probe_timeout_s=5)
        assert Supervisor(config).healthy() is True
    finally:
        server.shutdown()
        server.server_close()


# -- single instance ----------------------------------------------------------


def test_second_supervisor_cannot_take_the_lock(cfg):
    first, second = Supervisor(cfg), Supervisor(cfg)
    try:
        assert first.acquire_lock() is True
        assert second.acquire_lock() is False
    finally:
        first.release_lock()
    assert second.acquire_lock() is True
    second.release_lock()


def test_run_exits_at_once_when_another_supervisor_holds_the_lock(cfg, make):
    owner = Supervisor(cfg)
    assert owner.acquire_lock()
    try:
        assert make(command=SLEEPER).run() == 0
    finally:
        owner.release_lock()


# -- child lifecycle with real processes --------------------------------------


def test_a_dead_child_is_noticed_and_logged(cfg, make):
    supervisor = make(command=QUICK_EXIT)
    child = supervisor.start_child()
    assert supervisor.watch_child(child) < 30
    assert f"child pid={child.pid} EXITED rc=3" in _log(cfg)


def test_lost_listener_restarts_the_child_after_two_refusals(cfg, make):
    supervisor = make(command=SLEEPER, refused=True)
    child = supervisor.start_child()
    supervisor.watch_child(child)
    assert child.poll() is not None, "child must be killed"
    assert "refused (2/2)" in _log(cfg)
    assert "lost its listener" in _log(cfg)


def test_slow_child_gets_patience_before_a_restart(cfg, make):
    supervisor = make(replace(cfg, max_failed_probes=3), command=SLEEPER, healthy=False)
    child = supervisor.start_child()
    supervisor.watch_child(child)
    assert child.poll() is not None
    assert "health probe missed (3/3)" in _log(cfg)
    assert "health probe missed (4/3)" not in _log(cfg)


def test_young_child_is_not_judged_during_boot_grace(cfg, make):
    config = replace(cfg, boot_grace_s=3600, max_failed_probes=1)
    supervisor = make(config, command=QUICK_EXIT, healthy=False)
    supervisor.watch_child(supervisor.start_child())
    assert "health probe missed" not in _log(cfg)


def test_adopts_a_foreign_server_and_never_starts_a_child(cfg, make):
    supervisor = make(replace(cfg, adopt_poll_s=0), command=SLEEPER, port_open=True)
    assert supervisor.run(rounds=2) == 0
    assert "adopting" in _log(cfg)
    assert "started child" not in _log(cfg)


# -- auto-update --------------------------------------------------------------


def test_behind_checkout_stops_the_child_then_updates(cfg, make):
    run, calls = _update_runner("behind")
    supervisor = make(replace(cfg, update_interval_s=3600), command=SLEEPER, run_update=run)
    child = supervisor.start_child()
    supervisor.watch_child(child)
    assert calls == [["--check"], []]
    assert child.poll() is not None
    log = _log(cfg)
    assert log.index("stopping for the update") < log.index("update status=updated")


def test_up_to_date_check_leaves_the_child_running(cfg, make):
    run, calls = _update_runner("up_to_date")
    config = replace(cfg, update_interval_s=3600, max_failed_probes=2)
    supervisor = make(config, command=SLEEPER, run_update=run, healthy=False)
    supervisor.watch_child(supervisor.start_child())
    assert calls == [["--check"]]  # checked once, then not due again for an hour
    assert "stopping for the update" not in _log(cfg)


def test_update_is_attempted_during_a_crash_loop(cfg, make):
    # A bad release that dies at boot must still be able to pull its fix.
    run, calls = _update_runner("behind")
    config = replace(cfg, update_interval_s=3600, backoff_start_s=0, backoff_max_s=0)
    supervisor = make(config, command=QUICK_EXIT, run_update=run)
    supervisor.supervise_once(backoff=0)
    assert calls == [["--check"], []]


def test_first_update_waits_for_the_first_delay(cfg, make):
    run, calls = _update_runner("behind")
    config = replace(cfg, update_interval_s=3600, first_update_delay_s=3600, max_failed_probes=1)
    supervisor = make(config, command=SLEEPER, run_update=run, healthy=False)
    supervisor.watch_child(supervisor.start_child())
    assert calls == []


def test_a_target_whose_install_failed_is_not_retried(cfg, make):
    failed = {"status": "error", "deps_synced": False, "reason": "rolled back", **HEADS}
    run, calls = _update_runner("behind", failed)
    config = replace(cfg, update_interval_s=1e-9, max_failed_probes=1)
    supervisor = make(config, command=SLEEPER, run_update=run, healthy=False)
    supervisor.update_cycle(None)
    supervisor.update_cycle(None)
    # Second round: checked, saw the same bad target, did not update again.
    assert calls == [["--check"], [], ["--check"]]
    assert "failed before" in _log(cfg)


def test_update_runner_failure_is_logged_not_fatal(cfg, make):
    def broken(extra, timeout):
        raise subprocess.TimeoutExpired(cmd="levh update", timeout=1)

    config = replace(cfg, update_interval_s=3600, max_failed_probes=1)
    supervisor = make(config, command=SLEEPER, run_update=broken, healthy=False)
    supervisor.watch_child(supervisor.start_child())
    assert "update could not run" in _log(cfg)


def test_auto_update_off_never_calls_the_updater(cfg, make):
    run, calls = _update_runner("behind")
    supervisor = make(replace(cfg, max_failed_probes=1), command=SLEEPER, run_update=run, healthy=False)
    supervisor.watch_child(supervisor.start_child())
    assert calls == []


def test_log_rotation_keeps_one_previous_file(cfg, monkeypatch):
    monkeypatch.setattr(sup, "LOG_MAX_BYTES", 10)
    supervisor = Supervisor(cfg)
    supervisor.log("first line that is long enough")
    supervisor.log("second")
    assert (cfg.log_dir / "supervisor.log.1").exists()
    assert "second" in _log(cfg)


def test_child_is_stopped_fast(cfg, make):
    supervisor = make(command=SLEEPER)
    child = supervisor.start_child()
    started = time.monotonic()
    supervisor.stop_child(child, graceful=True)
    assert child.poll() is not None
    assert time.monotonic() - started < 30
