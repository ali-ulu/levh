"""``server/core/update_state.py``: the record of the last auto-update cycle.

The module exists because a skip used to be visible only in the supervisor's
own log (issue #522). These tests pin the two things a reader depends on: that
*every* reason ``levh update`` can produce maps to a copy-pasteable alternative,
and that the record says "needs attention" for exactly the outcomes that leave
the checkout behind.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from server.core import update_state


# -- the reason -> action mapping --------------------------------------------


def test_every_skip_reason_levh_update_emits_has_an_action():
    """The contract that keeps the report from becoming a dead end.

    A skip reason with no alternative is the report this issue exists to
    replace, so this walks every reason the command can produce and fails the
    moment one has no action.
    """
    reasons = [
        "not a git checkout (a wheel install updates with `pip install -U levh`)",
        "checkout is on 'fix/x', auto-update only follows 'main'",
        "HEAD is detached, auto-update only follows 'main'",
        "checkout is on 'release' which tracks 'origin/release'; auto-update only follows 'origin/main'",
        "local changes to tracked files; commit or discard them first",
        "local history diverged from origin/main; not merging unattended",
        "local history has local commits not on origin/main; not merging unattended",
    ]
    for reason in reasons:
        action = update_state.action_for(reason, branch="main")
        assert action, f"no action for reason: {reason!r}"


def test_branch_action_names_the_branch_that_is_checked_out():
    action = update_state.action_for(
        "checkout is on 'fix/supervisor-flapping-restart', auto-update only follows 'main'",
        branch="main",
    )
    assert "fix/supervisor-flapping-restart" in action
    assert "main" in action


def test_unknown_reason_still_gets_a_next_step():
    action = update_state.action_for("something git said that nobody classified", branch="main")
    assert "levh update --check" in action


def test_empty_reason_has_no_action():
    assert update_state.action_for("", branch="main") == ""


# -- needs_attention ----------------------------------------------------------


@pytest.mark.parametrize(
    "status,reason,expected",
    [
        ("up_to_date", "", False),
        ("updated", "", False),
        ("behind", "", True),
        ("error", "dependency install failed", True),
        ("skipped", "checkout is on 'fix/x', auto-update only follows 'main'", True),
        ("skipped", "local changes to tracked files; commit or discard them first", True),
        ("skipped", "not a git checkout (a wheel install updates with `pip install -U levh`)", False),
    ],
)
def test_needs_attention_classifies_each_outcome(status, reason, expected):
    assert update_state.needs_attention(status, reason) is expected


def test_a_skipped_checkout_is_attention_even_though_behind_is_zero():
    """The skip is decided before the fetch, so its ``behind`` is 0 by design."""
    state = update_state.build_update_state(
        {"status": "skipped", "reason": "checkout is on 'fix/x', auto-update only follows 'main'", "behind": 0}
    )
    assert state["behind"] == 0
    assert state["needs_attention"] is True


# -- round trip ---------------------------------------------------------------


def test_write_then_read_round_trips(tmp_path):
    path = tmp_path / "update-state.json"
    written = update_state.write_update_state(
        {"status": "skipped", "reason": "checkout is on 'fix/x', auto-update only follows 'main'"},
        path=path,
        branch="main",
        interval_hours=6.0,
    )
    assert json.loads(path.read_text(encoding="utf-8")) == written
    assert update_state.read_update_state(path=path) == written


def test_write_leaves_no_temp_file_behind(tmp_path):
    path = tmp_path / "update-state.json"
    update_state.write_update_state({"status": "up_to_date"}, path=path)
    assert [p.name for p in tmp_path.iterdir()] == ["update-state.json"]


def test_read_of_missing_or_malformed_state_is_none(tmp_path):
    assert update_state.read_update_state(path=tmp_path / "absent.json") is None
    broken = tmp_path / "update-state.json"
    broken.write_text("{not json", encoding="utf-8")
    assert update_state.read_update_state(path=broken) is None


def test_write_failure_is_not_fatal(tmp_path, monkeypatch):
    """A supervisor that cannot write its state must still supervise."""
    def _explode(*args, **kwargs):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(Path, "mkdir", _explode)
    state = update_state.write_update_state({"status": "up_to_date"}, path=tmp_path / "deep" / "s.json")
    assert state["status"] == "up_to_date"


# -- staleness ----------------------------------------------------------------


def test_a_stale_timer_counts_as_attention():
    """A state file that stops being rewritten means the supervisor stopped."""
    now = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
    old = now - timedelta(hours=20)
    state = update_state.build_update_state(
        {"status": "up_to_date"}, checked_at=old.isoformat(), interval_hours=6.0
    )
    summary = update_state.summarize(state, now=now)
    assert summary["stale"] is True
    assert summary["needs_attention"] is True


def test_a_fresh_state_is_not_stale():
    now = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
    state = update_state.build_update_state(
        {"status": "up_to_date"}, checked_at=(now - timedelta(hours=1)).isoformat(), interval_hours=6.0
    )
    assert update_state.summarize(state, now=now)["stale"] is False


def test_no_interval_configured_is_never_stale():
    """``LEVH_AUTO_UPDATE_HOURS=0`` disables the timer; its silence is expected."""
    state = update_state.build_update_state({"status": "up_to_date"}, interval_hours=0.0)
    assert update_state.summarize(state)["stale"] is False


# -- summarize ----------------------------------------------------------------


def test_absent_state_is_reported_as_not_present_and_not_a_problem():
    summary = update_state.summarize(None)
    assert summary["present"] is False
    assert summary["needs_attention"] is False


def test_summarize_surfaces_reason_and_action():
    state = update_state.build_update_state(
        {"status": "skipped", "reason": "checkout is on 'fix/x', auto-update only follows 'main'", "behind": 3}
    )
    summary = update_state.summarize(state)
    assert summary["present"] is True
    assert summary["behind"] == 3
    assert summary["needs_attention"] is True
    assert summary["action"]


# -- path resolution ----------------------------------------------------------


def test_state_path_follows_the_supervisor_log_dir_override():
    path = update_state.state_path(environ={"LEVH_SUPERVISOR_LOG_DIR": "/tmp/levh-logs"})
    assert path == Path("/tmp/levh-logs/update-state.json")


def test_state_file_sits_beside_the_supervisor_log():
    assert update_state.UPDATE_STATE_FILE == "update-state.json"
    from server.supervisor import Supervisor, SupervisorConfig

    supervisor = Supervisor(SupervisorConfig())
    assert supervisor.update_state_path == supervisor.sup_log.with_name("update-state.json")
