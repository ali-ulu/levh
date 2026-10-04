"""Durable collaboration primitives for Team Memory (#377).

This module deliberately does not decide truth. It gives agents two shared,
workspace-scoped coordination primitives:

* handoffs: explicit transfer of work/context from one agent to another;
* decisions: a durable ledger keyed by project + decision_key.

When two different active decisions target the same key, both become
contested. Nothing auto-wins: an admin resolves the contest explicitly.
That mirrors LEVH's existing conflict philosophy: signal, not verdict.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Callable

from .agent_identity import normalize_agent
from .database import Database
from .tenancy import AuthorizationError, authorize, current_workspace_id


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_statement(value: str) -> str:
    return " ".join((value or "").split())


class TeamMemoryService:
    """Workspace-scoped handoffs and shared decisions."""

    def __init__(self, db: Database, emit: Callable[[str, dict], None]):
        self.db = db
        self._emit = emit
        # One aiosqlite connection is shared by tasks in this process. Serialize
        # the read-decide-write transaction so one task's uniqueness rollback
        # cannot undo another task's still-uncommitted decision.
        self._decision_lock = asyncio.Lock()

    @staticmethod
    def _workspace() -> str:
        return current_workspace_id()

    @staticmethod
    def _decode_handoff(row) -> dict:
        out = dict(row)
        try:
            out["memory_ids"] = json.loads(out.pop("memory_ids_json") or "[]")
        except (TypeError, json.JSONDecodeError):
            out["memory_ids"] = []
            out.pop("memory_ids_json", None)
        return out

    async def create_handoff(
        self,
        *,
        project: str,
        to_agent: str,
        title: str,
        summary: str = "",
        memory_ids: list[str] | None = None,
    ) -> dict:
        actor = authorize("store", self._workspace())
        project = (project or "").strip()
        title = (title or "").strip()
        raw_target = (to_agent or "").strip()
        if not project:
            raise ValueError("project is required")
        if not title:
            raise ValueError("title is required")
        if not raw_target:
            raise ValueError("to_agent is required")
        target = normalize_agent(raw_target)

        row = {
            "id": uuid.uuid4().hex,
            "workspace_id": actor.workspace_id,
            "project": project,
            "from_principal_id": actor.id,
            "from_agent": normalize_agent(actor.agent) if actor.agent else None,
            "to_agent": target,
            "title": title,
            "summary": (summary or "").strip(),
            "memory_ids_json": json.dumps(memory_ids or []),
            "status": "pending",
            "created_at": _now(),
            "accepted_at": None,
            "accepted_by": None,
            "accepted_agent": None,
            "completed_at": None,
        }
        await self.db.conn.execute(
            """
            INSERT INTO team_handoffs
                (id, workspace_id, project, from_principal_id, from_agent,
                 to_agent, title, summary, memory_ids_json, status, created_at,
                 accepted_at, accepted_by, accepted_agent, completed_at)
            VALUES
                (:id, :workspace_id, :project, :from_principal_id, :from_agent,
                 :to_agent, :title, :summary, :memory_ids_json, :status, :created_at,
                 :accepted_at, :accepted_by, :accepted_agent, :completed_at)
            """,
            row,
        )
        await self.db.conn.commit()
        result = self._decode_handoff(row)
        self._emit("team_handoff_created", result)
        return result

    async def list_handoffs(
        self,
        *,
        project: str | None = None,
        status: str | None = None,
        to_agent: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        authorize("read", self._workspace())
        clauses = ["workspace_id = ?"]
        params: list[object] = [self._workspace()]
        if project:
            clauses.append("project = ?")
            params.append(project)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if to_agent:
            clauses.append("to_agent = ?")
            params.append(normalize_agent(to_agent))
        params.append(max(1, min(int(limit), 1000)))
        cursor = await self.db.conn.execute(
            f"""SELECT * FROM team_handoffs
                WHERE {' AND '.join(clauses)}
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?""",  # nosec B608 - clauses are fixed SQL fragments
            params,
        )
        return [self._decode_handoff(row) for row in await cursor.fetchall()]

    async def _handoff(self, handoff_id: str) -> dict | None:
        authorize("read", self._workspace())
        cursor = await self.db.conn.execute(
            "SELECT * FROM team_handoffs WHERE id = ? AND workspace_id = ?",
            (handoff_id, self._workspace()),
        )
        row = await cursor.fetchone()
        return self._decode_handoff(row) if row else None

    async def accept_handoff(self, handoff_id: str) -> dict:
        actor = authorize("update", self._workspace())
        handoff = await self._handoff(handoff_id)
        if not handoff:
            raise KeyError("handoff not found")
        if handoff["status"] != "pending":
            raise ValueError("handoff is not pending")
        actor_agent = normalize_agent(actor.agent) if actor.agent else None
        if actor_agent and handoff["to_agent"] not in (actor_agent, "*"):
            raise AuthorizationError("handoff is addressed to another agent")

        accepted_at = _now()
        cursor = await self.db.conn.execute(
            """
            UPDATE team_handoffs
               SET status = 'accepted',
                   accepted_at = ?,
                   accepted_by = ?,
                   accepted_agent = ?
             WHERE id = ? AND workspace_id = ? AND status = 'pending'
            """,
            (accepted_at, actor.id, actor_agent, handoff_id, actor.workspace_id),
        )
        await self.db.conn.commit()
        if cursor.rowcount != 1:
            raise ValueError("handoff state changed")
        result = await self._handoff(handoff_id)
        assert result is not None
        self._emit("team_handoff_accepted", result)
        return result

    async def complete_handoff(self, handoff_id: str) -> dict:
        actor = authorize("update", self._workspace())
        handoff = await self._handoff(handoff_id)
        if not handoff:
            raise KeyError("handoff not found")
        if handoff["status"] != "accepted":
            raise ValueError("handoff is not accepted")
        if actor.role != "admin" and handoff.get("accepted_by") != actor.id:
            raise AuthorizationError("only the accepting principal can complete this handoff")

        completed_at = _now()
        cursor = await self.db.conn.execute(
            """
            UPDATE team_handoffs
               SET status = 'completed', completed_at = ?
             WHERE id = ? AND workspace_id = ? AND status = 'accepted'
            """,
            (completed_at, handoff_id, actor.workspace_id),
        )
        await self.db.conn.commit()
        if cursor.rowcount != 1:
            raise ValueError("handoff state changed")
        result = await self._handoff(handoff_id)
        assert result is not None
        self._emit("team_handoff_completed", result)
        return result

    async def create_decision(
        self,
        *,
        project: str,
        decision_key: str,
        statement: str,
        rationale: str = "",
    ) -> dict:
        async with self._decision_lock:
            return await self._create_decision_locked(
                project=project,
                decision_key=decision_key,
                statement=statement,
                rationale=rationale,
            )

    async def _create_decision_locked(
        self,
        *,
        project: str,
        decision_key: str,
        statement: str,
        rationale: str = "",
    ) -> dict:
        actor = authorize("store", self._workspace())
        project = (project or "").strip()
        key = (decision_key or "").strip().lower()
        statement = _clean_statement(statement)
        if not project:
            raise ValueError("project is required")
        if not key:
            raise ValueError("decision_key is required")
        if not statement:
            raise ValueError("statement is required")

        cursor = await self.db.conn.execute(
            """
            SELECT * FROM team_decisions
             WHERE workspace_id = ? AND project = ? AND decision_key = ?
               AND status IN ('active', 'contested')
             ORDER BY created_at DESC, rowid DESC
            """,
            (actor.workspace_id, project, key),
        )
        current = [dict(row) for row in await cursor.fetchall()]

        for existing in current:
            if _clean_statement(existing["statement"]).casefold() == statement.casefold():
                return {
                    "created": False,
                    "contested": existing["status"] == "contested",
                    "decision": existing,
                    "conflicts": current if existing["status"] == "contested" else [],
                }

        contested = bool(current)
        conflict_group_id = None
        if contested:
            conflict_group_id = next(
                (
                    existing.get("conflict_group_id")
                    for existing in current
                    if existing.get("conflict_group_id")
                ),
                None,
            ) or current[-1]["id"]
            await self.db.conn.execute(
                """
                UPDATE team_decisions
                   SET status = 'contested',
                       conflict_group_id = COALESCE(conflict_group_id, ?)
                 WHERE workspace_id = ? AND project = ? AND decision_key = ?
                   AND status = 'active'
                """,
                (conflict_group_id, actor.workspace_id, project, key),
            )

        row = {
            "id": uuid.uuid4().hex,
            "workspace_id": actor.workspace_id,
            "project": project,
            "decision_key": key,
            "statement": statement,
            "rationale": (rationale or "").strip(),
            "status": "contested" if contested else "active",
            "created_by": actor.id,
            "created_agent": normalize_agent(actor.agent) if actor.agent else None,
            "created_at": _now(),
            "conflict_group_id": conflict_group_id,
            "superseded_by": None,
            "resolved_at": None,
            "resolved_by": None,
        }
        try:
            await self.db.conn.execute(
                """
                INSERT INTO team_decisions
                    (id, workspace_id, project, decision_key, statement, rationale,
                     status, created_by, created_agent, created_at, conflict_group_id,
                     superseded_by, resolved_at, resolved_by)
                VALUES
                    (:id, :workspace_id, :project, :decision_key, :statement, :rationale,
                     :status, :created_by, :created_agent, :created_at, :conflict_group_id,
                     :superseded_by, :resolved_at, :resolved_by)
                """,
                row,
            )
            await self.db.conn.commit()
        except sqlite3.IntegrityError:
            await self.db.conn.rollback()
            if contested:
                raise
            # A peer may have won the one-active-decision race after our read.
            # Re-evaluate against its decision; the retry will either dedupe or
            # create a contested proposal.
            return await self._create_decision_locked(
                project=project,
                decision_key=key,
                statement=statement,
                rationale=rationale,
            )

        if contested:
            conflicts = await self.list_decisions(
                project=project, decision_key=key, status="contested", limit=100
            )
            self._emit(
                "team_decision_contested",
                {
                    "project": project,
                    "decision_key": key,
                    "decision": row,
                    "conflicts": conflicts,
                },
            )
        else:
            conflicts = []
            self._emit("team_decision_created", row)
        return {
            "created": True,
            "contested": contested,
            "decision": row,
            "conflicts": conflicts,
        }

    async def list_decisions(
        self,
        *,
        project: str | None = None,
        decision_key: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        authorize("read", self._workspace())
        clauses = ["workspace_id = ?"]
        params: list[object] = [self._workspace()]
        if project:
            clauses.append("project = ?")
            params.append(project)
        if decision_key:
            clauses.append("decision_key = ?")
            params.append(decision_key.strip().lower())
        if status:
            clauses.append("status = ?")
            params.append(status)
        params.append(max(1, min(int(limit), 1000)))
        cursor = await self.db.conn.execute(
            f"""SELECT * FROM team_decisions
                WHERE {' AND '.join(clauses)}
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?""",  # nosec B608 - clauses are fixed SQL fragments
            params,
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def resolve_decision(self, decision_id: str) -> dict:
        actor = authorize("configure", self._workspace())
        cursor = await self.db.conn.execute(
            "SELECT * FROM team_decisions WHERE id = ? AND workspace_id = ?",
            (decision_id, actor.workspace_id),
        )
        chosen_row = await cursor.fetchone()
        if not chosen_row:
            raise KeyError("decision not found")
        chosen = dict(chosen_row)
        if chosen["status"] != "contested":
            raise ValueError("decision is not contested")

        resolved_at = _now()
        await self.db.conn.execute(
            """
            UPDATE team_decisions
               SET status = 'superseded',
                   superseded_by = ?,
                   resolved_at = ?,
                   resolved_by = ?
             WHERE workspace_id = ? AND project = ? AND decision_key = ?
               AND status = 'contested' AND id <> ?
            """,
            (
                decision_id,
                resolved_at,
                actor.id,
                actor.workspace_id,
                chosen["project"],
                chosen["decision_key"],
                decision_id,
            ),
        )
        await self.db.conn.execute(
            """
            UPDATE team_decisions
               SET status = 'active',
                   superseded_by = NULL,
                   resolved_at = ?,
                   resolved_by = ?
             WHERE id = ? AND workspace_id = ? AND status = 'contested'
            """,
            (resolved_at, actor.id, decision_id, actor.workspace_id),
        )
        await self.db.conn.commit()
        cursor = await self.db.conn.execute(
            "SELECT * FROM team_decisions WHERE id = ? AND workspace_id = ?",
            (decision_id, actor.workspace_id),
        )
        resolved_row = await cursor.fetchone()
        assert resolved_row is not None
        result = dict(resolved_row)
        self._emit("team_decision_resolved", result)
        return result
