"""Agent tracking services: presence/heartbeat, checkpoints and usage.

Split out of the old ``AgentTracker`` god-class (#99). Each service owns one
responsibility and receives its collaborators through the constructor
(``db``, ``emit``) so nothing reaches for process-global state.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Callable

from .agent_identity import agent_display, agent_icon, normalize_agent
from .database import Database


def _row_to_dict(row) -> dict:
    """Convert a database row to a dictionary."""
    if hasattr(row, "keys"):
        return dict(row)
    return {}


class AgentPresenceService:
    """Connection lifecycle and presence detection.

    Tracks agent sessions (connect/heartbeat/disconnect), in-memory presence
    with a heartbeat timeout, and aggregates activity/stats over sessions.
    """

    def __init__(self, db: Database, emit: Callable[[str, dict], None]):
        self.db = db
        self._emit = emit
        # In-memory presence: agent_id → last_heartbeat timestamp
        self._presence: dict[str, float] = {}
        # Heartbeat timeout: agent considered offline after this many seconds
        self.heartbeat_timeout = 120  # 2 minutes

    async def initialize(self) -> None:
        """Create the agent tracking tables."""
        from .agent_tracker import _AGENT_TRACKING_SCHEMA

        await self.db.conn.executescript(_AGENT_TRACKING_SCHEMA)
        await self.db.conn.commit()

    async def agent_connect(
        self,
        agent_name: str,
        session_id: str | None = None,
        project: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        """Record an agent connecting. Returns the agent session record."""
        import uuid

        key = normalize_agent(agent_name)
        now_iso = datetime.now(timezone.utc).isoformat()
        agent_session_id = uuid.uuid4().hex

        # Close any previous active connection for this agent
        await self._close_stale_connections(key)

        row = {
            "id": agent_session_id,
            "agent_name": key,
            "agent_display": agent_display(agent_name),
            "session_id": session_id,
            "project": project,
            "status": "connected",
            "connected_at": now_iso,
            "last_heartbeat_at": now_iso,
            "disconnected_at": None,
            "metadata_json": json.dumps(metadata or {}),
        }

        await self.db.conn.execute(
            """INSERT INTO agent_sessions
               (id, agent_name, agent_display, session_id, project,
                status, connected_at, last_heartbeat_at, disconnected_at, metadata_json)
               VALUES (:id, :agent_name, :agent_display, :session_id, :project,
                       :status, :connected_at, :last_heartbeat_at, :disconnected_at, :metadata_json)""",
            row,
        )
        await self.db.conn.commit()

        self._presence[agent_session_id] = time.time()
        self._emit("agent_connected", {
            "agent_session_id": agent_session_id,
            "agent_name": key,
            "display": agent_display(agent_name),
            "session_id": session_id,
            "project": project,
        })

        return {
            "agent_session_id": agent_session_id,
            "agent_name": key,
            "display": agent_display(agent_name),
            "icon": agent_icon(agent_name),
            "session_id": session_id,
            "project": project,
            "connected_at": now_iso,
        }

    async def heartbeat(self, agent_session_id: str) -> dict:
        """Update the last heartbeat for a connected agent."""
        now_iso = datetime.now(timezone.utc).isoformat()
        await self.db.conn.execute(
            "UPDATE agent_sessions SET last_heartbeat_at = ? WHERE id = ? AND status = 'connected'",
            (now_iso, agent_session_id),
        )
        await self.db.conn.commit()
        self._presence[agent_session_id] = time.time()
        return {"ok": True, "agent_session_id": agent_session_id, "last_heartbeat": now_iso}

    async def agent_disconnect(self, agent_session_id: str) -> dict:
        """Record an agent disconnecting."""
        now_iso = datetime.now(timezone.utc).isoformat()
        await self.db.conn.execute(
            "UPDATE agent_sessions SET status = 'disconnected', disconnected_at = ? WHERE id = ?",
            (now_iso, agent_session_id),
        )
        await self.db.conn.commit()
        self._presence.pop(agent_session_id, None)
        self._emit("agent_disconnected", {"agent_session_id": agent_session_id})
        return {"ok": True, "agent_session_id": agent_session_id, "disconnected_at": now_iso}

    async def _close_stale_connections(self, agent_name: str) -> None:
        """Close any existing active connections for this agent."""
        await self.db.conn.execute(
            """UPDATE agent_sessions SET status = 'stale', disconnected_at = ?
               WHERE agent_name = ? AND status = 'connected'""",
            (datetime.now(timezone.utc).isoformat(), agent_name),
        )

    def is_online(self, agent_session_id: str) -> bool:
        """Check if an agent is still considered online (recent heartbeat)."""
        last = self._presence.get(agent_session_id)
        if last is None:
            return False
        return (time.time() - last) < self.heartbeat_timeout

    async def get_online_agents(self) -> list[dict]:
        """Return all currently online agents."""
        now = time.time()
        online_ids = [
            aid for aid, ts in self._presence.items()
            if (now - ts) < self.heartbeat_timeout
        ]
        if not online_ids:
            return []

        placeholders = ",".join("?" for _ in online_ids)
        cursor = await self.db.conn.execute(
            f"SELECT * FROM agent_sessions WHERE id IN ({placeholders}) AND status = 'connected'",
            online_ids,
        )
        rows = await cursor.fetchall()
        return [_row_to_dict(r) for r in rows]

    async def get_agent_activity(self, limit: int = 100) -> list[dict]:
        """Return recent agent sessions (active and disconnected)."""
        cursor = await self.db.conn.execute(
            "SELECT * FROM agent_sessions ORDER BY connected_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            d = _row_to_dict(row)
            # Enrich with online status
            d["online"] = self.is_online(d["id"]) if d["status"] == "connected" else False
            result.append(d)
        return result

    async def get_agent_stats(self) -> dict:
        """Aggregate statistics about agent usage."""
        # Total connections
        cursor = await self.db.conn.execute("SELECT COUNT(*) FROM agent_sessions")
        total = (await cursor.fetchone())[0]

        # By agent
        cursor = await self.db.conn.execute(
            """SELECT agent_name, agent_display,
                      COUNT(*) as connection_count,
                      MIN(connected_at) as first_seen,
                      MAX(connected_at) as last_seen
               FROM agent_sessions
               GROUP BY agent_name
               ORDER BY connection_count DESC"""
        )
        by_agent = [dict(r) for r in await cursor.fetchall()]

        # Currently online
        online = await self.get_online_agents()

        # Sessions created per agent
        cursor = await self.db.conn.execute(
            """SELECT a.agent_name, COUNT(DISTINCT a.session_id) as session_count
               FROM agent_sessions a
               WHERE a.session_id IS NOT NULL
               GROUP BY a.agent_name"""
        )
        sessions_by_agent = {r[0]: r[1] for r in await cursor.fetchall()}

        return {
            "total_connections": total,
            "currently_online": len(online),
            "online_agents": [
                {"agent_name": a["agent_name"], "display": a["agent_display"]}
                for a in online
            ],
            "by_agent": [
                {**a, "sessions": sessions_by_agent.get(a["agent_name"], 0)}
                for a in by_agent
            ],
        }


class AgentCheckpointService:
    """Checkpoint snapshots of agent work state."""

    def __init__(self, db: Database, emit: Callable[[str, dict], None]):
        self.db = db
        self._emit = emit

    async def create_checkpoint(
        self,
        agent_name: str,
        title: str,
        summary: str = "",
        session_id: str | None = None,
        project: str | None = None,
        checkpoint_type: str = "auto",
        memory_ids: list[str] | None = None,
    ) -> dict:
        """Create a checkpoint — a snapshot of important work state."""
        import uuid

        now_iso = datetime.now(timezone.utc).isoformat()
        checkpoint_id = uuid.uuid4().hex

        row = {
            "id": checkpoint_id,
            "agent_name": normalize_agent(agent_name),
            "session_id": session_id,
            "project": project,
            "checkpoint_type": checkpoint_type,
            "title": title,
            "summary": summary,
            "memory_ids_json": json.dumps(memory_ids or []),
            "created_at": now_iso,
        }

        await self.db.conn.execute(
            """INSERT INTO agent_checkpoints
               (id, agent_name, session_id, project, checkpoint_type,
                title, summary, memory_ids_json, created_at)
               VALUES (:id, :agent_name, :session_id, :project, :checkpoint_type,
                       :title, :summary, :memory_ids_json, :created_at)""",
            row,
        )
        await self.db.conn.commit()

        self._emit("checkpoint_created", {
            "checkpoint_id": checkpoint_id,
            "agent_name": row["agent_name"],
            "title": title,
            "project": project,
        })

        return {
            "checkpoint_id": checkpoint_id,
            "agent_name": row["agent_name"],
            "title": title,
            "created_at": now_iso,
        }

    async def list_checkpoints(
        self,
        agent_name: str | None = None,
        project: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """List recent checkpoints."""
        conditions = []
        params: list[Any] = []

        if agent_name:
            conditions.append("agent_name = ?")
            params.append(normalize_agent(agent_name))
        if project:
            conditions.append("project = ?")
            params.append(project)

        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        params.append(limit)

        cursor = await self.db.conn.execute(
            f"SELECT * FROM agent_checkpoints{where} ORDER BY created_at DESC LIMIT ?",
            params,
        )
        return [dict(r) for r in await cursor.fetchall()]


class AgentUsageService:
    """Cross-cutting usage/billing and collaboration queries.

    Combines session and checkpoint data; presence is injected so these
    read-only analytics stay in one place.
    """

    def __init__(
        self,
        db: Database,
        emit: Callable[[str, dict], None],
        presence: AgentPresenceService,
    ):
        self.db = db
        self._emit = emit
        self._presence = presence

    async def _session_agent_names(self, session_ids: list[str]) -> dict[str, str]:
        """Map agent session ids to their canonical agent name.

        One query resolves every id, so callers can decide ownership without
        a per-session round trip.
        """
        ids = [sid for sid in session_ids if sid]
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        cursor = await self.db.conn.execute(
            f"SELECT id, agent_name FROM agent_sessions WHERE id IN ({placeholders})",
            ids,
        )
        return {row["id"]: row["agent_name"] for row in await cursor.fetchall()}

    async def get_agent_metrics(self, agent_name: str) -> dict:
        """Get performance metrics for a specific agent."""
        key = normalize_agent(agent_name)

        # Connection stats
        cursor = await self.db.conn.execute(
            """SELECT COUNT(*) as total,
                      COUNT(DISTINCT session_id) as sessions,
                      MIN(connected_at) as first_seen,
                      MAX(connected_at) as last_seen
               FROM agent_sessions WHERE agent_name = ?""",
            (key,),
        )
        conn_stats = dict(await cursor.fetchone())

        # Checkpoint stats
        cursor = await self.db.conn.execute(
            """SELECT COUNT(*) as checkpoints,
                      checkpoint_type,
                      COUNT(CASE WHEN checkpoint_type='auto' THEN 1 END) as auto_checkpoints,
                      COUNT(CASE WHEN checkpoint_type='manual' THEN 1 END) as manual_checkpoints
               FROM agent_checkpoints WHERE agent_name = ?
               GROUP BY checkpoint_type""",
            (key,),
        )
        cp_rows = await cursor.fetchall()
        cp_stats = {r["checkpoint_type"]: dict(r) for r in cp_rows} if cp_rows else {}

        # Online status — count only sessions that both have a fresh heartbeat
        # and actually belong to this agent (the session id alone is opaque).
        now = time.time()
        online_ids = [
            aid for aid, ts in self._presence._presence.items()
            if (now - ts) < self._presence.heartbeat_timeout
        ]
        online_names = await self._session_agent_names(online_ids)
        online_count = sum(1 for aid in online_ids if online_names.get(aid) == key)

        return {
            "agent_name": key,
            "display": agent_display(agent_name),
            "connections": conn_stats["total"],
            "sessions": conn_stats["sessions"],
            "first_seen": conn_stats["first_seen"],
            "last_seen": conn_stats["last_seen"],
            "checkpoints": cp_stats,
            "currently_online": online_count > 0,
        }

    async def get_usage_billing(self) -> dict:
        """Get usage billing metrics for all agents."""
        # Connection counts
        cursor = await self.db.conn.execute(
            """SELECT agent_name,
                      COUNT(*) as connections,
                      COUNT(DISTINCT session_id) as sessions
               FROM agent_sessions
               GROUP BY agent_name
               ORDER BY connections DESC"""
        )
        agents = [dict(r) for r in await cursor.fetchall()]

        # Checkpoint counts
        cursor = await self.db.conn.execute(
            """SELECT agent_name,
                      COUNT(*) as checkpoints
               FROM agent_checkpoints
               GROUP BY agent_name"""
        )
        cp_counts = {r["agent_name"]: r["checkpoints"] for r in await cursor.fetchall()}

        # Total usage
        total_connections = sum(a["connections"] for a in agents)
        total_sessions = sum(a["sessions"] for a in agents)
        total_checkpoints = sum(cp_counts.values())

        return {
            "summary": {
                "total_connections": total_connections,
                "total_sessions": total_sessions,
                "total_checkpoints": total_checkpoints,
            },
            "by_agent": [
                {
                    **a,
                    "checkpoints": cp_counts.get(a["agent_name"], 0),
                    "cost_estimate": a["connections"] * 0.01 + a["sessions"] * 0.05,
                }
                for a in agents
            ],
        }

    async def get_project_collaboration(self, project: str) -> dict:
        """Get collaboration info for agents working on the same project."""
        # Active agents on this project
        cursor = await self.db.conn.execute(
            """SELECT DISTINCT agent_name, agent_display, status, last_heartbeat_at
               FROM agent_sessions
               WHERE project = ?
               ORDER BY last_heartbeat_at DESC""",
            (project,),
        )
        agents = [dict(r) for r in await cursor.fetchall()]

        # Add online status — a project agent is online if any of its live
        # presence entries belongs to it (presence keys are session ids).
        online_ids = [
            aid for aid in self._presence._presence if self._presence.is_online(aid)
        ]
        online_names = await self._session_agent_names(online_ids)
        active_names = set(online_names.values())
        for agent in agents:
            agent["online"] = normalize_agent(agent["agent_name"]) in active_names

        # Shared checkpoints
        cursor = await self.db.conn.execute(
            """SELECT agent_name, title, created_at
               FROM agent_checkpoints
               WHERE project = ?
               ORDER BY created_at DESC
               LIMIT 10""",
            (project,),
        )
        shared_checkpoints = [dict(r) for r in await cursor.fetchall()]

        return {
            "project": project,
            "agents": agents,
            "shared_checkpoints": shared_checkpoints,
            "collaboration_score": len([a for a in agents if a["online"]]),
        }