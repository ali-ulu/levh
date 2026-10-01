"""Mistake guard — turn a corrected mistake into a rule that outlives the session.

The durable half of the guard. A mistake becomes two things: a **rule** stored
as a pinned memory, and a **violation** row recording the incident that taught
it. Pinned memories are exempt from H(x,ψ) decay (see
:class:`server.core.hscore.HScoreCalculator`), so the rule is still there weeks
later, in a different session, for a different model.

This module deliberately stops at recording and reading back. Deciding whether
a *proposed* action violates a rule is a different problem — it runs on the hot
path in front of every tool call, so it needs a latency budget and a
false-positive story that recorded data can inform but this layer cannot
assume.

That line was drawn in an earlier release; :meth:`GuardService.check_action`
is the first thing to cross it, and it does so on the terms the paragraph
above set. The matching itself lives in :mod:`server.core.action_gate` as a
pure function — no model, no network — and the verdict it returns is
*advisory*: ``warn`` or ``allow``, never ``block``. Whether a warning is an
instruction stays the caller's policy, so this layer still does not decide
what an agent is permitted to do.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from . import action_gate
from .database import Database
from .memory_engine import MemoryEngine
from .types import RULE_TAG, Memory

SEVERITIES = ("low", "medium", "high", "critical")
DEFAULT_SEVERITY = "medium"

# How much a mistake raises the rule's importance. Critical mistakes should
# outrank ordinary pinned notes when a context file has to pick a few.
_SEVERITY_IMPORTANCE = {
    "low": 0.75,
    "medium": 0.85,
    "high": 0.92,
    "critical": 1.0,
}


class GuardService:
    """Record mistakes as pinned rules and read the incident log back."""

    def __init__(self, db: Database, engine: MemoryEngine) -> None:
        self.db = db
        self.engine = engine

    @staticmethod
    def _normalize_severity(severity: str) -> str:
        value = (severity or "").strip().lower()
        return value if value in SEVERITIES else DEFAULT_SEVERITY

    @staticmethod
    def _rule_statement(wrong_action: str, correct_action: str, root_cause: str) -> str:
        """Compose the sentence that gets injected into future sessions.

        Written as an instruction rather than a narrative: the reader is a
        model deciding what to do next, not a person reviewing history.
        """
        parts = [f"Do not {wrong_action.strip().rstrip('.')}."]
        if correct_action.strip():
            parts.append(f"Instead: {correct_action.strip().rstrip('.')}.")
        if root_cause.strip():
            parts.append(f"(Root cause: {root_cause.strip().rstrip('.')}.)")
        return " ".join(parts)

    async def record_mistake(
        self,
        task: str,
        wrong_action: str,
        correct_action: str,
        root_cause: str = "",
        tool_name: str = "",
        severity: str = DEFAULT_SEVERITY,
        source: str = "user",
        project: str | None = None,
    ) -> dict:
        """Store a mistake as a pinned rule plus a violation row."""
        if not wrong_action.strip():
            raise ValueError("wrong_action is required")
        if not correct_action.strip():
            raise ValueError("correct_action is required")

        level = self._normalize_severity(severity)
        statement = self._rule_statement(wrong_action, correct_action, root_cause)

        rule: Memory = await self.engine.store(
            content=statement,
            importance=_SEVERITY_IMPORTANCE[level],
            tags=[RULE_TAG],
            memory_type="episodic",
            pinned=True,
            project=project,
            source=f"guard:{source}",
            metadata={
                "guard_rule": True,
                "task": task,
                "wrong_action": wrong_action,
                "correct_action": correct_action,
                "root_cause": root_cause,
                "severity": level,
            },
        )

        violation = {
            "id": f"v_{uuid.uuid4().hex[:12]}",
            "rule_id": rule.id,
            "task": task or None,
            "wrong_action": wrong_action,
            "root_cause": root_cause or None,
            "tool_name": tool_name or None,
            "severity": level,
            "source": source or "user",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "resolved": 0,
            "resolution": None,
        }
        await self.db.insert_violation(violation)

        return {
            "rule_id": rule.id,
            "violation_id": violation["id"],
            "statement": statement,
            "pinned": True,
            "severity": level,
            "total_violations": await self.db.count_violations(),
        }

    async def list_violations(
        self,
        days: int | None = None,
        severity: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Read the incident log, newest first."""
        since = None
        if days is not None and days > 0:
            since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        return await self.db.list_violations(
            since=since,
            severity=self._normalize_severity(severity) if severity else None,
            limit=max(1, min(limit, 500)),
        )

    async def list_rules(
        self,
        project: str | None = None,
        limit: int = 50,
        include_global: bool = True,
    ) -> list[Memory]:
        """Return the pinned rules mistakes have produced, most important first.

        ``project`` narrows the list to rules recorded against that project
        **plus the global rules recorded without one**. The merge is the point:
        ``search_memories`` filters ``project = ?`` exactly, so a rule recorded
        without a project would otherwise be invisible to a project-scoped
        caller — and a global rule is the *most* general kind, not the least.
        A mistake like force-pushing to ``main`` must warn everywhere.

        The merge is pushed into the query (``include_global``) rather than
        applied to the results, because the query applies a ``LIMIT``: a page
        fetched first and filtered in Python can be filled entirely by pinned
        memories that are not rules at all, and the global rule that applies
        would never reach ``check_action``. A memory is a rule only when it
        carries ``RULE_TAG``, which is a Python-side test, so the SQL filter
        still admits non-rules — the ``limit * 4`` headroom is what covers
        them, and the scoping above is what keeps the headroom from being spent
        on other projects.

        Pass ``include_global=False`` for a strict single-project view. Exact
        project filtering is untouched for every other memory search; the merge
        lives here, in the guard.
        """
        pinned = await self.engine.episodic.search(
            project=project,
            # The global rules are folded into the query itself, before its
            # LIMIT, so unrelated pinned rows in other projects cannot crowd
            # out the global rule that actually applies.
            include_global=project is not None and include_global,
            pinned=True,
            limit=max(limit * 4, 100),
        )
        rules = [m for m in pinned if RULE_TAG in (m.tags or [])]
        rules.sort(key=lambda m: (m.importance, m.created_at), reverse=True)
        return rules[:limit]

    async def check_action(
        self,
        tool_name: str,
        action_text: str,
        project: str | None = None,
        limit: int = 200,
    ) -> dict:
        """Judge a *proposed* action against the rules on record.

        Read-only: it touches no counter, no decay clock and no violation row.
        A gate that mutates the store would make the act of asking a question
        change the answer, and it would run on every tool call.

        ``project`` scopes the rules the way :meth:`list_rules` does: rules
        recorded against that project **plus the global ones recorded without
        a project**. A rule with no project is the most general kind — a
        mistake about force-pushing to ``main`` must warn everywhere — so
        scoping narrows the gate without hiding it from the rules that apply
        to everyone.

        The verdict is advisory (see :mod:`server.core.action_gate`): ``warn``
        when a rule overlaps the action, ``allow`` otherwise. Never ``block``.
        """
        rules = await self.list_rules(project=project, limit=limit)
        verdict = action_gate.check_action(
            tool_name,
            action_text,
            (
                {
                    "id": m.id,
                    "statement": m.content,
                    "task": (m.metadata or {}).get("task", ""),
                    "wrong_action": (m.metadata or {}).get("wrong_action", ""),
                    "severity": (m.metadata or {}).get("severity", DEFAULT_SEVERITY),
                }
                for m in rules
            ),
        )
        verdict["tool_name"] = tool_name
        verdict["project"] = project
        return verdict
