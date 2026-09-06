"""Agent Tracker — Real-time tracking of connected AI agents.

Tracks which agents are connected, their sessions, activity timestamps,
and checkpoints. Provides presence detection via WebSocket heartbeats
and a REST API for the dashboard.

Data lives in SQLite alongside memories — no external service needed.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from .database import Database
from .types import Session, SessionStatus


# ── Agent types (known clients) ──────────────────────────────────────

KNOWN_AGENTS: dict[str, dict[str, str]] = {
    "claude-code": {"display": "Claude Code", "icon": "🤖"},
    "claude-desktop": {"display": "Claude Desktop", "icon": "🧠"},
    "cursor": {"display": "Cursor", "icon": "⚡"},
    "vscode": {"display": "VS Code", "icon": "💻"},
    "windsurf": {"display": "Windsurf", "icon": "🌊"},
    "cline": {"display": "Cline", "icon": "🔧"},
    "jcode": {"display": "jcode", "icon": "📝"},
    "omp": {"display": "oh-my-pi", "icon": "🥧"},
    "opencode": {"display": "opencode", "icon": "🔓"},
    "codex": {"display": "Codex", "icon": "🤖"},
    "hermes": {"display": "Hermes", "icon": "🏛️"},
    "connector": {"display": "Connector", "icon": "🔗"},
    "dashboard": {"display": "Dashboard", "icon": "📊"},
    "cli": {"display": "CLI", "icon": "⌨️"},
    "mcp-client": {"display": "MCP Client", "icon": "🔌"},
    "auto-connect": {"display": "Auto-Connect", "icon": "🔀"},
    "api": {"display": "REST API", "icon": "🌐"},
    "unknown": {"display": "Unknown Agent", "icon": "❓"},
}


def normalize_agent(agent_name: str) -> str:
    """Normalize agent name to a canonical key."""
    name = (agent_name or "").strip().lower()
    # Aliases
    aliases = {
        "claude": "claude-code",
        "claude_desktop": "claude-desktop",
        "claudecode": "claude-code",
        "claudedesktop": "claude-desktop",
        "oh_my_pi": "omp",
        "ohmypi": "omp",
        "vs_code": "vscode",
        "visualstudiocode": "vscode",
    }
    return aliases.get(name, name) if name else "unknown"


def agent_display(agent_name: str) -> str:
    """Human-readable display name for an agent."""
    key = normalize_agent(agent_name)
    info = KNOWN_AGENTS.get(key, {})
    return info.get("display", agent_name or "Unknown")


def agent_icon(agent_name: str) -> str:
    """Emoji icon for an agent."""
    key = normalize_agent(agent_name)
    return KNOWN_AGENTS.get(key, {}).get("icon", "❓")


# ── Database schema for agent tracking ───────────────────────────────

_AGENT_TRACKING_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_sessions (
    id TEXT PRIMARY KEY,
    agent_name TEXT NOT NULL,
    agent_display TEXT NOT NULL,
    session_id TEXT,
    project TEXT,
    status TEXT NOT NULL DEFAULT 'connected',
    connected_at TEXT NOT NULL,
    last_heartbeat_at TEXT NOT NULL,
    disconnected_at TEXT,
    metadata_json TEXT DEFAULT '{}',
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS agent_checkpoints (
    id TEXT PRIMARY KEY,
    agent_name TEXT NOT NULL,
    session_id TEXT,
    project TEXT,
    checkpoint_type TEXT NOT NULL DEFAULT 'auto',
    title TEXT,
    summary TEXT,
    memory_ids_json TEXT DEFAULT '[]',
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_agent_sessions_agent ON agent_sessions(agent_name);
CREATE INDEX IF NOT EXISTS idx_agent_sessions_status ON agent_sessions(status);
CREATE INDEX IF NOT EXISTS idx_agent_sessions_connected ON agent_sessions(connected_at);
CREATE INDEX IF NOT EXISTS idx_agent_checkpoints_agent ON agent_checkpoints(agent_name);
CREATE INDEX IF NOT EXISTS idx_agent_checkpoints_created ON agent_checkpoints(created_at);
"""


class AgentTracker:
    """Tracks connected AI agents, their sessions, and checkpoints."""

    def __init__(self, db: Database, emit: Callable[[str, dict], None]):
        self.db = db
        self._emit = emit
        # In-memory presence: agent_id → last_heartbeat timestamp
        self._presence: dict[str, float] = {}
        # Heartbeat timeout: agent considered offline after this many seconds
        self.heartbeat_timeout = 120  # 2 minutes

    async def initialize(self) -> None:
        """Create the agent tracking tables."""
        await self.db.conn.executescript(_AGENT_TRACKING_SCHEMA)
        await self.db.conn.commit()

    # ── Connection lifecycle ──────────────────────────────────────────

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

    # ── Presence queries ──────────────────────────────────────────────

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
        return [self._row_to_dict(r) for r in rows]

    async def get_agent_activity(self, limit: int = 100) -> list[dict]:
        """Return recent agent sessions (active and disconnected)."""
        cursor = await self.db.conn.execute(
            "SELECT * FROM agent_sessions ORDER BY connected_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            d = self._row_to_dict(row)
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

    # ── Checkpoints ───────────────────────────────────────────────────

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


    # ── Agent Performance Metrics ──────────────────────────────────────

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
        
        # Online status
        online_count = sum(
            1 for aid, ts in self._presence.items()
            if (time.time() - ts) < self.heartbeat_timeout
            and self._is_agent_session(aid, key)
        )
        
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
    
    def _is_agent_session(self, session_id: str, agent_name: str) -> bool:
        """Check if a session belongs to an agent (in-memory check)."""
        # This is a simplified check - in production we'd query the DB
        return True  # Placeholder - actual implementation would check DB

    # ── Usage Billing ─────────────────────────────────────────────────

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

    # ── Agent Collaboration ──────────────────────────────────────────

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
        
        # Add online status
        for agent in agents:
            agent["online"] = any(
                self.is_online(aid)
                for aid in self._presence
                if self._is_agent_session(aid, agent["agent_name"])
            )
        
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
    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _row_to_dict(row) -> dict:
        """Convert a database row to a dictionary."""
        if hasattr(row, "keys"):
            return dict(row)
        return {}
