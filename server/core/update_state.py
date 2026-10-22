"""The last auto-update outcome, and whether it needs a human (issue #522).

``server.supervisor`` runs ``levh update --check`` every
``LEVH_AUTO_UPDATE_HOURS`` and, when the checkout is behind, applies it. When it
cannot — a checkout on another branch, local edits to tracked files, a history
that diverged — it skips and says so *in its own log*, and only there. The
server then keeps running an increasingly stale commit while every surface a
user actually looks at reports nothing: ``levh doctor`` says OK, ``/api/health``
says ``ok``, and the dashboard shows a green badge. That silence is the bug.

This module is the one place the outcome is recorded and turned into a report,
so the supervisor that writes it and the surfaces that read it — ``levh
doctor``, ``/api/health``, ``/api/readyz`` — cannot drift on what "needs
attention" means. It is pure bookkeeping: no git, no network, no engine.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

#: Written beside the supervisor log, so the operator who finds one finds the
#: other. Same directory, same override (``LEVH_SUPERVISOR_LOG_DIR``).
UPDATE_STATE_FILE = "update-state.json"

#: A state file older than this many update intervals is no longer describing
#: the current timer — the supervisor has stopped running, which is itself a
#: reason the checkout is not being updated.
STALE_INTERVAL_MULTIPLIER = 2.0

#: The one alternative a skip reason must name, keyed by the *kind* of refusal.
#: Each string is a copy-pasteable action, because "it skipped" without "here is
#: what to run" is the report this issue exists to replace.
_ACTIONS: dict[str, str] = {
    "branch": "run `levh update --branch {current}` to follow it, or `git checkout {branch}` to resume auto-updates",
    "dirty": "commit or discard the local changes, then auto-updates resume",
    "diverged": "reconcile with {remote}/{branch} by hand; auto-update will not merge unattended",
    "ahead": "push or drop the local commits; auto-update will not merge unattended",
    "not_checkout": "a wheel install updates with `pip install -U levh`",
    "unknown": "run `levh update --check` to see why",
}

#: The update command's own wording, parsed only for the names it already
#: carries. Each pattern is anchored on a phrase the command emits verbatim.
_BRANCH_RE = re.compile(r"checkout is on '([^']+)', auto-update only follows '([^']+)'")
_DETACHED_RE = re.compile(r"HEAD is detached, auto-update only follows '([^']+)'")
_TRACKS_RE = re.compile(r"checkout is on '([^']+)' which tracks '[^']+'; auto-update only follows '([^']+)'")
_HISTORY_RE = re.compile(r"(diverged from|local commits not on) (\S+)")


def default_state_dir() -> Path:
    """Where the supervisor keeps its log and its update state.

    Mirrors the supervisor's own resolution (``LEVH_SUPERVISOR_LOG_DIR``, else
    ``%LOCALAPPDATA%\\LevH`` on Windows, else ``~/.local/state/levh``) so the
    server process — the supervisor's child, which inherits the environment —
    reads the same file the supervisor wrote.
    """
    override = os.environ.get("LEVH_SUPERVISOR_LOG_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "LevH"
    return Path.home() / ".local" / "state" / "levh"


def state_path(*, environ: Mapping[str, str] | None = None) -> Path:
    """The update-state file for the given environment (default: this one)."""
    if environ is None:
        return default_state_dir() / UPDATE_STATE_FILE
    override = environ.get("LEVH_SUPERVISOR_LOG_DIR")
    if override:
        return Path(override) / UPDATE_STATE_FILE
    if sys.platform == "win32" and environ.get("LOCALAPPDATA"):
        return Path(environ["LOCALAPPDATA"]) / "LevH" / UPDATE_STATE_FILE
    return Path.home() / ".local" / "state" / "levh" / UPDATE_STATE_FILE


def action_for(reason: str, *, branch: str) -> str:
    """The copy-pasteable alternative to *reason*, or ``""`` when none applies.

    The reason string is the update command's own wording, so this classifies it
    rather than re-deriving the state: one source of truth for *why* (the
    command), one for *what to do about it* (here), and the two are pinned
    together by a test that walks every reason the command can produce.

    Branch names are read back out of the reason when it carries them, so the
    action names the branch that is actually checked out — not the configured
    default, which the operator may already have overridden with ``--branch``.
    """
    lowered = reason.lower()
    if "not a git checkout" in lowered:
        return _ACTIONS["not_checkout"]
    match = _DETACHED_RE.search(reason)
    if match:
        return _ACTIONS["branch"].format(current="(detached)", branch=match.group(1))
    match = _TRACKS_RE.search(reason)
    if match:
        return _ACTIONS["branch"].format(current=match.group(1), branch=match.group(2))
    match = _BRANCH_RE.search(reason)
    if match:
        return _ACTIONS["branch"].format(current=match.group(1), branch=match.group(2))
    if "local changes" in lowered:
        return _ACTIONS["dirty"]
    match = _HISTORY_RE.search(reason)
    if match:
        kind = "diverged" if "diverged" in match.group(1) else "ahead"
        remote_branch = match.group(2)
        remote, _, target = remote_branch.partition("/")
        return _ACTIONS[kind].format(remote=remote or "origin", branch=target or branch)
    if reason:
        return _ACTIONS["unknown"]
    return ""


def _current_branch_from_reason(reason: str) -> str:
    """The branch the checkout is actually on, read from the skip reason."""
    for pattern in (_BRANCH_RE, _TRACKS_RE):
        match = pattern.search(reason)
        if match:
            return match.group(1)
    return ""


def needs_attention(status: str, reason: str) -> bool:
    """Whether this outcome means auto-update is not keeping the checkout current.

    A ``behind`` check is an update that is due and was not applied. ``error``
    is one that could not run. ``skipped`` is the silent case this issue is
    about: the update refuses and the checkout stays where it is.

    The skip is decided *before* the fetch (branch, dirty tree, diverged
    history), so its ``behind`` count is zero by construction — the checkout
    can be many commits stale and still report ``skipped``. The condition is
    therefore structural: a skip means updates are not being applied, whatever
    the distance. The one exception is a wheel install, which has no checkout
    to update and is not expected to have one.
    """
    if status in ("behind", "error"):
        return True
    if status == "skipped":
        return "not a git checkout" not in reason.lower()
    return False


def build_update_state(
    result: Mapping[str, object],
    *,
    branch: str = "main",
    current_branch: str = "",
    checked_at: str | None = None,
    interval_hours: float = 0.0,
) -> dict[str, object]:
    """The record written after one update cycle.

    Everything a reader needs is resolved *here*, at write time, so a reader
    never has to re-derive it from a reason string: the action to take, whether
    it needs attention, and when it was last checked.
    """
    status = str(result.get("status", "") or "")
    reason = str(result.get("reason", "") or "")
    behind = _as_int(result.get("behind"))
    current = current_branch or _current_branch_from_reason(reason)
    return {
        "checked_at": checked_at or datetime.now(timezone.utc).isoformat(),
        "status": status,
        "reason": reason,
        "old_head": str(result.get("old_head", "") or ""),
        "new_head": str(result.get("new_head", "") or ""),
        "behind": behind,
        "branch": branch,
        "current_branch": current,
        "deps_synced": result.get("deps_synced"),
        "interval_hours": interval_hours,
        "needs_attention": needs_attention(status, reason),
        "action": action_for(reason, branch=branch),
    }


def write_update_state(
    result: Mapping[str, object],
    *,
    path: Path | None = None,
    branch: str = "main",
    current_branch: str = "",
    checked_at: str | None = None,
    interval_hours: float = 0.0,
) -> dict[str, object]:
    """Persist one update cycle's outcome; return the record written.

    Writing is best-effort: a supervisor that cannot write its state must still
    supervise, so an unwritable directory is not an error here.
    """
    state = build_update_state(
        result,
        branch=branch,
        current_branch=current_branch,
        checked_at=checked_at,
        interval_hours=interval_hours,
    )
    target = path or state_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename so a reader never sees a half-written file: the
        # server polls this on every health check.
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        pass
    return state


def read_update_state(
    *,
    path: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object] | None:
    """The last written state, or ``None`` when there is nothing to report.

    A missing or malformed file is not an error: an install that never ran the
    supervisor has no auto-update state, and that is the common case.
    """
    target = path or state_path(environ=environ)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def _as_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _as_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _parse_instant(text: object) -> datetime | None:
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        parsed = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def is_stale(state: Mapping[str, object], *, now: datetime | None = None) -> bool:
    """Whether the record is older than two update intervals.

    A state file that stops being rewritten means the supervisor stopped
    running, so the "up to date" it last reported is no longer evidence. Two
    intervals of slack absorbs one missed timer tick before calling it stale.
    """
    interval_hours = _as_float(state.get("interval_hours"))
    if interval_hours <= 0:
        return False
    checked = _parse_instant(state.get("checked_at"))
    if checked is None:
        return False
    current = now or datetime.now(timezone.utc)
    age_hours = (current - checked).total_seconds() / 3600.0
    return age_hours > interval_hours * STALE_INTERVAL_MULTIPLIER


def summarize(
    state: Mapping[str, object] | None,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    """The report ``levh doctor``, ``/api/health`` and ``/api/readyz`` share.

    ``present`` is false for an install that never ran the supervisor — which is
    normal, not a problem, so a reader must not warn on it. ``needs_attention``
    folds in staleness: a stale "up to date" from a supervisor that stopped is
    the same silent staleness this issue is about.
    """
    if not state:
        return {
            "present": False,
            "status": "unknown",
            "behind": 0,
            "reason": "",
            "action": "",
            "needs_attention": False,
            "stale": False,
            "checked_at": None,
        }
    stale = is_stale(state, now=now)
    attention = bool(state.get("needs_attention")) or stale
    reason = str(state.get("reason", "") or "")
    action = str(state.get("action", "") or "")
    checked_at = state.get("checked_at")
    if stale and not reason:
        reason = "the auto-update timer has stopped reporting"
    return {
        "present": True,
        "status": str(state.get("status", "") or "unknown"),
        "behind": _as_int(state.get("behind")),
        "reason": reason,
        "action": action,
        "needs_attention": attention,
        "stale": stale,
        "checked_at": checked_at if isinstance(checked_at, str) else None,
    }
