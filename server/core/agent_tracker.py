"""Agent Tracker — Real-time tracking of connected AI agents.

Tracks which agents are connected, their sessions, activity timestamps,
and checkpoints. Provides presence detection via WebSocket heartbeats
and a REST API for the dashboard.

Data lives in SQLite alongside memories — no external service needed.

The class is deliberately a thin facade over the dedicated services in
:mod:`.agent_services` (presence, checkpoints, usage) and the identity
helpers in :mod:`.agent_identity`; see #99.
"""

from __future__ import annotations

from typing import Callable

from .agent_identity import agent_display, agent_icon, normalize_agent  # noqa: F401
from .agent_services import (
    AgentCheckpointService,
    AgentPresenceService,
    AgentUsageService,
)
from .database import Database
from .team_memory import TeamMemoryService


# ── Database schema for agent tracking ───────────────────────────────

_AGENT_TRACKING_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_sessions (
    id TEXT PRIMARY KEY,
    agent_name TEXT NOT NULL,
    agent_display TEXT NOT NULL,
    session_id TEXT,
    project TEXT,
    workspace_id TEXT NOT NULL DEFAULT 'default',
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
    workspace_id TEXT NOT NULL DEFAULT 'default',
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

_AGENT_TENANCY_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_agent_sessions_workspace
    ON agent_sessions(workspace_id, project, status, last_heartbeat_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_checkpoints_workspace
    ON agent_checkpoints(workspace_id, project, created_at DESC);
"""


class AgentTracker:
    """Thin facade over presence/checkpoint/usage services (#99)."""

    def __init__(self, db: Database, emit: Callable[[str, dict], None]):
        self.db = db
        self._emit = emit
        self.presence = AgentPresenceService(db, emit)
        self.checkpoints = AgentCheckpointService(db, emit)
        self.usage = AgentUsageService(db, emit, self.presence)
        self.team = TeamMemoryService(db, emit)

    async def initialize(self) -> None:
        """Create the agent tracking tables."""
        await self.presence.initialize()

    # ── Presence / connection lifecycle ──────────────────────────────

    async def agent_connect(self, agent_name, session_id=None, project=None, metadata=None):
        """Record an agent connecting. Returns the agent session record."""
        return await self.presence.agent_connect(
            agent_name, session_id=session_id, project=project, metadata=metadata
        )

    async def heartbeat(self, agent_session_id: str) -> dict:
        """Update the last heartbeat for a connected agent."""
        return await self.presence.heartbeat(agent_session_id)

    async def agent_disconnect(self, agent_session_id: str) -> dict:
        """Record an agent disconnecting."""
        return await self.presence.agent_disconnect(agent_session_id)

    def is_online(self, agent_session_id: str) -> bool:
        """Check if an agent is still considered online (recent heartbeat)."""
        return self.presence.is_online(agent_session_id)

    async def get_online_agents(self) -> list[dict]:
        """Return all currently online agents."""
        return await self.presence.get_online_agents()

    async def get_agent_activity(self, limit: int = 100) -> list[dict]:
        """Return recent agent sessions (active and disconnected)."""
        return await self.presence.get_agent_activity(limit=limit)

    async def get_agent_stats(self) -> dict:
        """Aggregate statistics about agent usage."""
        return await self.presence.get_agent_stats()

    # ── Checkpoints ──────────────────────────────────────────────────

    async def create_checkpoint(
        self,
        agent_name,
        title,
        summary="",
        session_id=None,
        project=None,
        checkpoint_type="auto",
        memory_ids=None,
    ) -> dict:
        """Create a checkpoint — a snapshot of important work state."""
        return await self.checkpoints.create_checkpoint(
            agent_name,
            title,
            summary=summary,
            session_id=session_id,
            project=project,
            checkpoint_type=checkpoint_type,
            memory_ids=memory_ids,
        )

    async def list_checkpoints(self, agent_name=None, project=None, limit=50) -> list[dict]:
        """List recent checkpoints."""
        return await self.checkpoints.list_checkpoints(
            agent_name=agent_name, project=project, limit=limit
        )

    # ── Usage / billing / collaboration ──────────────────────────────

    async def get_agent_metrics(self, agent_name: str) -> dict:
        """Get performance metrics for a specific agent."""
        return await self.usage.get_agent_metrics(agent_name)

    async def get_usage_billing(self) -> dict:
        """Get usage billing metrics for all agents."""
        return await self.usage.get_usage_billing()

    async def get_project_collaboration(self, project: str) -> dict:
        """Get live presence plus durable collaboration state for a project."""
        result = await self.usage.get_project_collaboration(project)
        handoffs = await self.team.list_handoffs(project=project, limit=20)
        decisions = await self.team.list_decisions(project=project, limit=20)
        decision_conflicts = await self.team.list_decision_conflicts(
            project=project,
            status="open",
            limit=20,
        )
        result["handoffs"] = handoffs
        result["decisions"] = decisions
        result["decision_conflicts"] = decision_conflicts
        result["pending_handoffs"] = sum(1 for h in handoffs if h["status"] == "pending")
        result["contested_decisions"] = sum(
            1 for d in decisions if d["status"] == "contested"
        )
        result["open_decision_conflicts"] = len(decision_conflicts)
        return result

    async def create_handoff(self, **kwargs) -> dict:
        return await self.team.create_handoff(**kwargs)

    async def list_handoffs(self, **kwargs) -> list[dict]:
        return await self.team.list_handoffs(**kwargs)

    async def accept_handoff(self, handoff_id: str) -> dict:
        return await self.team.accept_handoff(handoff_id)

    async def complete_handoff(self, handoff_id: str) -> dict:
        return await self.team.complete_handoff(handoff_id)

    async def create_team_decision(self, **kwargs) -> dict:
        return await self.team.create_decision(**kwargs)

    async def list_team_decisions(self, **kwargs) -> list[dict]:
        return await self.team.list_decisions(**kwargs)

    async def resolve_team_decision(self, decision_id: str) -> dict:
        return await self.team.resolve_decision(decision_id)

    async def detect_team_decision_conflicts(self, **kwargs) -> dict:
        return await self.team.detect_decision_conflicts(**kwargs)

    async def list_team_decision_conflicts(self, **kwargs) -> list[dict]:
        return await self.team.list_decision_conflicts(**kwargs)

    async def review_team_decision_conflict(self, conflict_id: str, action: str) -> dict:
        return await self.team.review_decision_conflict(conflict_id, action)