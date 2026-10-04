"""Deterministic handoff matching, claiming, and dispatch for Team Memory (#377).

The scheduler is intentionally local and explainable:
- agent capabilities come from agent_sessions.metadata_json;
- handoff requirements live on the durable handoff row;
- explicit claims are self-service;
- automatic dispatch is admin-triggered and opt-in per agent.

No LLM decides ownership. Matching is exact capability-set containment plus
workspace/project boundaries, priority, current load, and stable tie-breaks.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Callable

from .agent_identity import normalize_agent
from .agent_services import AgentPresenceService
from .database import Database
from .team_memory import TeamMemoryService
from .tenancy import AuthorizationError, authorize, current_workspace_id


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _caps(values) -> list[str]:
    if not isinstance(values, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip().casefold()
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _metadata(row: dict) -> dict:
    raw = row.get("metadata_json")
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _capacity(value) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 1
    return max(1, min(parsed, 32))


class TeamSchedulerService:
    """Capability-aware scheduler over online agent sessions and handoffs."""

    def __init__(
        self,
        db: Database,
        emit: Callable[[str, dict], None],
        presence: AgentPresenceService,
    ) -> None:
        self.db = db
        self._emit = emit
        self._presence = presence
        self._claim_lock = asyncio.Lock()

    @staticmethod
    def _workspace() -> str:
        return current_workspace_id()

    async def _profiles(self, *, project: str | None = None) -> list[dict]:
        rows = await self._presence.get_online_agents()
        profiles: list[dict] = []
        for row in rows:
            if project and row.get("project") != project:
                continue
            metadata = _metadata(row)
            profiles.append(
                {
                    "agent_session_id": row["id"],
                    "agent_name": normalize_agent(row["agent_name"]),
                    "project": row.get("project"),
                    "capabilities": _caps(metadata.get("capabilities")),
                    "scheduler_enabled": _bool(
                        metadata.get("scheduler_enabled"),
                        default=False,
                    ),
                    "max_parallel_handoffs": _capacity(
                        metadata.get("max_parallel_handoffs", 1)
                    ),
                }
            )
        return profiles

    async def _load(self, session_id: str) -> int:
        cursor = await self.db.conn.execute(
            """
            SELECT COUNT(*) FROM team_handoffs
             WHERE workspace_id = ? AND accepted_session_id = ?
               AND status = 'accepted'
            """,
            (self._workspace(), session_id),
        )
        row = await cursor.fetchone()
        return int(row[0] if row else 0)

    @staticmethod
    def _requirements(handoff: dict) -> set[str]:
        return set(_caps(handoff.get("required_capabilities", [])))

    @classmethod
    def _matches(cls, handoff: dict, profile: dict) -> bool:
        if handoff.get("project") != profile.get("project"):
            return False
        target = handoff.get("to_agent")
        if target not in ("*", profile.get("agent_name")):
            return False
        return cls._requirements(handoff).issubset(
            set(profile.get("capabilities", []))
        )

    async def _pending_handoffs(
        self,
        *,
        project: str | None = None,
        wildcard_only: bool = False,
        limit: int = 1000,
    ) -> list[dict]:
        authorize("read", self._workspace())
        clauses = ["workspace_id = ?", "status = 'pending'"]
        params: list[object] = [self._workspace()]
        if project:
            clauses.append("project = ?")
            params.append(project)
        if wildcard_only:
            clauses.append("to_agent = '*'")
        params.append(max(1, min(int(limit), 1000)))
        cursor = await self.db.conn.execute(
            f"""SELECT * FROM team_handoffs
                WHERE {' AND '.join(clauses)}
                ORDER BY priority DESC, created_at ASC, rowid ASC
                LIMIT ?""",  # nosec B608 - clauses are fixed SQL fragments
            params,
        )
        return [
            TeamMemoryService._decode_handoff(row)
            for row in await cursor.fetchall()
        ]

    async def list_matches(
        self,
        *,
        project: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Preview deterministic matches without mutating handoffs."""
        authorize("read", self._workspace())
        profiles = await self._profiles(project=project)
        handoffs = await self._pending_handoffs(project=project, limit=1000)
        loads = {
            profile["agent_session_id"]: await self._load(
                profile["agent_session_id"]
            )
            for profile in profiles
        }

        matches: list[dict] = []
        for handoff in handoffs:
            for profile in profiles:
                if not self._matches(handoff, profile):
                    continue
                load = loads[profile["agent_session_id"]]
                if load >= profile["max_parallel_handoffs"]:
                    continue
                matches.append(
                    {
                        "handoff": handoff,
                        "agent_session_id": profile["agent_session_id"],
                        "agent_name": profile["agent_name"],
                        "capabilities": profile["capabilities"],
                        "current_load": load,
                        "max_parallel_handoffs": profile[
                            "max_parallel_handoffs"
                        ],
                        "scheduler_enabled": profile["scheduler_enabled"],
                    }
                )

        matches.sort(
            key=lambda item: (
                -int(item["handoff"].get("priority", 0)),
                int(item["current_load"]),
                item["handoff"].get("created_at") or "",
                item["agent_name"],
                item["agent_session_id"],
            )
        )
        return matches[: max(1, min(int(limit), 1000))]

    async def _claim_row(
        self,
        handoff: dict,
        profile: dict,
        *,
        accepted_by: str,
    ) -> dict | None:
        accepted_at = _now()
        cursor = await self.db.conn.execute(
            """
            UPDATE team_handoffs
               SET status = 'accepted',
                   accepted_at = ?,
                   accepted_by = ?,
                   accepted_agent = ?,
                   accepted_session_id = ?
             WHERE id = ? AND workspace_id = ? AND status = 'pending'
            """,
            (
                accepted_at,
                accepted_by,
                profile["agent_name"],
                profile["agent_session_id"],
                handoff["id"],
                self._workspace(),
            ),
        )
        if cursor.rowcount != 1:
            return None
        await self.db.conn.commit()
        result = dict(handoff)
        result.update(
            {
                "status": "accepted",
                "accepted_at": accepted_at,
                "accepted_by": accepted_by,
                "accepted_agent": profile["agent_name"],
                "accepted_session_id": profile["agent_session_id"],
            }
        )
        return result

    async def claim_next(self, agent_session_id: str) -> dict:
        async with self._claim_lock:
            return await self._claim_next_locked(agent_session_id)

    async def _claim_next_locked(self, agent_session_id: str) -> dict:
        """Atomically claim the best eligible handoff for one online agent."""
        actor = authorize("update", self._workspace())
        profiles = await self._profiles()
        profile = next(
            (
                item
                for item in profiles
                if item["agent_session_id"] == agent_session_id
            ),
            None,
        )
        if profile is None:
            raise KeyError("agent session is not online")

        actor_agent = normalize_agent(actor.agent) if actor.agent else None
        if actor.role != "admin":
            if not actor_agent:
                raise AuthorizationError(
                    "non-admin claim requires an agent-bound principal"
                )
            if actor_agent != profile["agent_name"]:
                raise AuthorizationError(
                    "agent principal cannot claim for another agent session"
                )

        load = await self._load(agent_session_id)
        if load >= profile["max_parallel_handoffs"]:
            return {
                "claimed": False,
                "reason": "capacity",
                "handoff": None,
            }

        handoffs = await self._pending_handoffs(
            project=profile.get("project"),
            limit=1000,
        )
        for handoff in handoffs:
            if not self._matches(handoff, profile):
                continue
            claimed = await self._claim_row(
                handoff,
                profile,
                accepted_by=actor.id,
            )
            if claimed is None:
                continue
            self._emit(
                "team_handoff_claimed",
                {
                    "handoff_id": claimed["id"],
                    "agent_session_id": agent_session_id,
                    "agent_name": profile["agent_name"],
                },
            )
            return {
                "claimed": True,
                "reason": None,
                "handoff": claimed,
            }

        return {
            "claimed": False,
            "reason": "no_match",
            "handoff": None,
        }

    async def dispatch(
        self,
        *,
        project: str | None = None,
        limit: int = 100,
    ) -> dict:
        async with self._claim_lock:
            return await self._dispatch_locked(project=project, limit=limit)

    async def _dispatch_locked(
        self,
        *,
        project: str | None = None,
        limit: int = 100,
    ) -> dict:
        """Admin-triggered deterministic auto-assignment of wildcard handoffs."""
        actor = authorize("configure", self._workspace())
        profiles = [
            profile
            for profile in await self._profiles(project=project)
            if profile["scheduler_enabled"]
        ]
        loads = {
            profile["agent_session_id"]: await self._load(
                profile["agent_session_id"]
            )
            for profile in profiles
        }
        handoffs = await self._pending_handoffs(
            project=project,
            wildcard_only=True,
            limit=limit,
        )

        assignments: list[dict] = []
        unassigned = 0
        for handoff in handoffs:
            eligible = [
                profile
                for profile in profiles
                if self._matches(handoff, profile)
                and loads[profile["agent_session_id"]]
                < profile["max_parallel_handoffs"]
            ]
            if not eligible:
                unassigned += 1
                continue
            eligible.sort(
                key=lambda profile: (
                    loads[profile["agent_session_id"]],
                    profile["agent_name"],
                    profile["agent_session_id"],
                )
            )
            winner = eligible[0]
            claimed = await self._claim_row(
                handoff,
                winner,
                accepted_by=actor.id,
            )
            if claimed is None:
                unassigned += 1
                continue
            loads[winner["agent_session_id"]] += 1
            assignment = {
                "handoff": claimed,
                "agent_session_id": winner["agent_session_id"],
                "agent_name": winner["agent_name"],
            }
            assignments.append(assignment)
            self._emit("team_handoff_dispatched", assignment)

        return {
            "assigned": len(assignments),
            "unassigned": unassigned,
            "assignments": assignments,
        }
