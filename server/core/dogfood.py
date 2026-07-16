"""Local dogfood journal (2.25) — usage signals for the productization gate.

An append-only JSONL file of coarse usage events, recorded so the Editor can
answer "does this actually help?" with numbers instead of vibes. Hard rules:

  - **local-only** — plain file next to the database; this module performs no
    network I/O of any kind and imports no HTTP/socket machinery.
  - **no default telemetry** — nothing ships anywhere; nothing is collected
    unless the journal is explicitly attached/recorded by the running install.
  - **no raw memory content** — events carry an event type, a timestamp, and
    a small whitelist of scalar attributes (ids, counts, labels). Content,
    queries, and any free text are rejected at the API boundary.
  - **export is a user action** — ``export()`` writes an *aggregate* report;
    raw event lines never leave the journal file unless the user copies it.

Determinism: ``record()`` takes the timestamp as a parameter (``when``);
callers on the live path pass wall-clock, tests pass fixed values.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from server.core.env import get_env

EVENT_TYPES = frozenset(
    {
        "memory_stored",
        "memory_recalled",
        "recall_helpful",
        "recall_not_helpful",
        "trust_viewed",
        "conflict_dismissed",
        "conflict_confirmed",
        "meeting_prep_opened",
        "briefing_opened",
        "review_keep",
        "review_reinforce",
        "review_weaken",
        "review_forget",
        "seed_demo_completed",
    }
)

# Attributes an event may carry. Deliberately excludes anything that could
# hold memory content or query text.
ALLOWED_ATTRS = frozenset({"memory_id", "conflict_id", "count", "label", "project"})

DEFAULT_JOURNAL_PATH = "./dogfood_events.jsonl"

# Live wiring is OPT-IN: nothing is journaled unless the user sets this env
# var to a truthy value. This is the "no default telemetry" rule in code.
ENABLED_ENV = "LEVH_DOGFOOD_ENABLED"

# The first of these marks "setup done"; deltas to the first occurrence of the
# others are the time-to-first-value product metrics.
_FIRST_VALUE_EVENTS = (
    "memory_recalled",
    "briefing_opened",
    "meeting_prep_opened",
)


def resolve_journal_path(
    explicit_path: str | os.PathLike | None = None,
    db_path: str | os.PathLike | None = None,
) -> str:
    """Resolve one canonical dogfood journal path for every caller.

    Precedence is intentionally shared by the live engine provider and the
    CLI readers so they cannot silently diverge:

    1. explicit ``--journal`` / function argument
    2. ``DOGFOOD_JOURNAL_PATH``
    3. sibling of ``SQLITE_DB_PATH`` / supplied ``db_path``
    4. ``./dogfood_events.jsonl``
    """
    if explicit_path:
        return str(Path(explicit_path))
    env_journal = os.getenv("DOGFOOD_JOURNAL_PATH")
    if env_journal:
        return env_journal
    resolved_db = db_path or os.getenv("SQLITE_DB_PATH")
    if resolved_db:
        return str(Path(resolved_db).resolve().parent / "dogfood_events.jsonl")
    return DEFAULT_JOURNAL_PATH


def journal_path() -> str:
    """Backward-compatible accessor using the canonical resolver."""
    return resolve_journal_path()


def dogfood_enabled() -> bool:
    """Live instrumentation flag. Defaults to OFF."""
    return get_env(ENABLED_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def default_journal_path_for(db_path: str | os.PathLike | None) -> str:
    """Journal location when none is configured: DOGFOOD_JOURNAL_PATH env if
    set, else ``dogfood_events.jsonl`` next to the SQLite database file."""
    return resolve_journal_path(db_path=db_path)


def maybe_attach(engine) -> "DogfoodJournal | None":
    """Wire the dogfood journal to a live engine — but ONLY when the user has
    opted in via STACKMEMORY_DOGFOOD_ENABLED. Called from the shared engine
    provider so every transport (REST API, MCP stdio/SSE, `serve`) gets the
    same behavior. Returns the journal when attached, else None. Never makes
    network calls; the journal is a local file next to the database."""
    if not dogfood_enabled():
        return None
    db_path = getattr(getattr(engine, "db", None), "db_path", None)
    journal = DogfoodJournal(resolve_journal_path(db_path=db_path))
    journal.attach(engine)
    return journal


class DogfoodJournal:
    """Append-only local JSONL journal of dogfood events."""

    def __init__(self, path: str | os.PathLike | None = None):
        self.path = Path(path or journal_path())

    # ── Writing ───────────────────────────────────────────────────
    def record(
        self,
        event_type: str,
        when: str | None = None,
        attrs: dict | None = None,
    ) -> dict:
        """Append one event. ``when`` is an ISO timestamp (injected for
        determinism; defaults to wall-clock UTC). ``attrs`` is restricted to
        ALLOWED_ATTRS scalars — anything else raises, so memory content can't
        be journaled by accident."""
        if event_type not in EVENT_TYPES:
            raise ValueError(
                f"unknown dogfood event '{event_type}'; valid: {sorted(EVENT_TYPES)}"
            )
        clean_attrs: dict = {}
        for key, value in (attrs or {}).items():
            if key not in ALLOWED_ATTRS:
                raise ValueError(
                    f"attribute '{key}' not allowed in dogfood events "
                    f"(allowed: {sorted(ALLOWED_ATTRS)})"
                )
            if not isinstance(value, (str, int, float, bool)) or (
                isinstance(value, str) and len(value) > 120
            ):
                raise ValueError(f"attribute '{key}' must be a short scalar")
            clean_attrs[key] = value
        event = {
            "event": event_type,
            "at": when or datetime.now(timezone.utc).isoformat(),
            **({"attrs": clean_attrs} if clean_attrs else {}),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def attach(self, engine) -> bool:
        """Subscribe to a MemoryEngine's event stream and journal the coarse
        signals. Only ids/counts cross over — payload content is dropped here,
        at the boundary, not downstream.

        Idempotent per engine: a second attach to the same engine is a no-op
        (returns False) so events are never double-journaled."""
        if getattr(engine, "_dogfood_attached", False):
            return False

        review_events = {
            "keep": "review_keep",
            "reinforce": "review_reinforce",
            "weaken": "review_weaken",
            "forget": "review_forget",
        }

        def _listener(event: str, payload: dict) -> None:
            if event == "stored":
                self.record("memory_stored", attrs={"memory_id": str(payload.get("id", ""))})
            elif event == "recalled":
                self.record("memory_recalled", attrs={"count": int(payload.get("count", 0))})
            elif event == "briefed":
                self.record("briefing_opened", attrs={"count": int(payload.get("recent_total", 0))})
            elif event == "meeting_prepped":
                self.record("meeting_prep_opened")
            elif event == "trust_viewed":
                self.record("trust_viewed", attrs={"memory_id": str(payload.get("memory_id", ""))})
            elif event == "reviewed":
                name = review_events.get(payload.get("action", ""))
                if name:
                    self.record(name, attrs={"memory_id": str(payload.get("memory_id", ""))})
            elif event == "demo_seeded":
                self.record("seed_demo_completed", attrs={"count": int(payload.get("seeded", 0))})
            elif event == "conflict_reviewed":
                action = payload.get("action")
                if action == "dismiss":
                    self.record("conflict_dismissed", attrs={"conflict_id": str(payload.get("id", ""))})
                elif action == "confirm":
                    self.record("conflict_confirmed", attrs={"conflict_id": str(payload.get("id", ""))})

        engine.subscribe(_listener)
        engine._dogfood_attached = True
        return True

    # ── Reading / aggregation ─────────────────────────────────────
    def events(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def status(self) -> dict:
        """Aggregate view: event counts, span, and time-to-first-value
        metrics. Contains counts/timestamps only — never event attributes,
        so nothing id- or content-shaped reaches an exported report."""
        events = self.events()
        counts: dict[str, int] = {}
        first_at: dict[str, str] = {}
        for ev in events:
            etype = ev.get("event", "unknown")
            counts[etype] = counts.get(etype, 0) + 1
            if etype not in first_at:
                first_at[etype] = ev.get("at", "")

        def _seconds_between(a: str | None, b: str | None) -> float | None:
            if not a or not b:
                return None
            try:
                da = datetime.fromisoformat(a)
                db = datetime.fromisoformat(b)
            except ValueError:
                return None
            return round((db - da).total_seconds(), 3)

        start = events[0].get("at") if events else None
        recalls = counts.get("memory_recalled", 0)
        helpful = counts.get("recall_helpful", 0)
        not_helpful = counts.get("recall_not_helpful", 0)
        rated = helpful + not_helpful
        review_total = sum(
            counts.get(k, 0)
            for k in ("review_keep", "review_reinforce", "review_weaken", "review_forget")
        )
        return {
            "journal_path": str(self.path),
            "total_events": len(events),
            "event_counts": dict(sorted(counts.items())),
            "first_event_at": start,
            "last_event_at": events[-1].get("at") if events else None,
            "time_to_first": {
                f"time_to_first_{name.replace('memory_', '').replace('_opened', '')}_seconds": _seconds_between(
                    start, first_at.get(name)
                )
                for name in _FIRST_VALUE_EVENTS
            },
            "recall_feedback": {
                "recalls": recalls,
                "rated": rated,
                "helpful_rate": round(helpful / rated, 4) if rated else None,
            },
            "review_distribution": {
                k.replace("review_", ""): counts.get(k, 0)
                for k in ("review_keep", "review_reinforce", "review_weaken", "review_forget")
                if counts.get(k)
            },
            "review_total": review_total,
            "seed_demo_completed": counts.get("seed_demo_completed", 0) > 0,
        }

    def export(self, output_path: str | os.PathLike) -> dict:
        """Explicit user action: write the *aggregate* status report to
        ``output_path``. Raw event lines (which may carry memory ids) stay in
        the journal file."""
        report = self.status()
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False)
        return report
