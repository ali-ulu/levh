"""MCP Tools: Agent tracking, presence, and checkpoint management.

These tools let agents connect, disconnect, heartbeat, create checkpoints,
and query agent activity. They power the Agent Activity dashboard.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    @mcp.tool()
    async def agent_connect(
        agent_name: str = "",
        session_id: str = "",
        project: str = "",
    ) -> str:
        """Connect this agent to LEVH and create a tracking session.

        Call this at the start of your session to register your presence.
        LEVH will track your activity and show you in the Agent Dashboard.

        Args:
            agent_name: Your agent name (e.g. "claude-code", "cursor", "vscode").
            session_id: Optional LEVH session ID to link to.
            project: Optional project/workspace name.
        """
        tracker = engine.agent_tracker
        if not tracker:
            return "Agent tracker not available."

        result = await tracker.agent_connect(
            agent_name=agent_name or "unknown",
            session_id=session_id or None,
            project=project or None,
        )

        return (
            f"Connected to LEVH as {result['display']} {result['icon']}\n"
            f"  Agent Session ID: {result['agent_session_id']}\n"
            f"  Project: {result.get('project') or 'none'}\n"
            f"\nUse this ID for heartbeats and disconnect."
        )

    @mcp.tool()
    async def agent_heartbeat(agent_session_id: str = "") -> str:
        """Send a heartbeat to keep your agent connection alive.

        Call this periodically (e.g. every 60 seconds) to show you're active.
        If no heartbeat is received for 2 minutes, you'll be marked offline.

        Args:
            agent_session_id: Your agent session ID from agent_connect.
        """
        tracker = engine.agent_tracker
        if not tracker:
            return "Agent tracker not available."

        if not agent_session_id:
            return "agent_session_id is required. Call agent_connect first."

        result = await tracker.heartbeat(agent_session_id)
        return f"Heartbeat sent at {result['last_heartbeat']}"

    @mcp.tool()
    async def agent_disconnect(agent_session_id: str = "") -> str:
        """Disconnect this agent from LEVH.

        Call this when your session ends to cleanly disconnect.

        Args:
            agent_session_id: Your agent session ID from agent_connect.
        """
        tracker = engine.agent_tracker
        if not tracker:
            return "Agent tracker not available."

        if not agent_session_id:
            return "agent_session_id is required."

        result = await tracker.agent_disconnect(agent_session_id)
        return f"Disconnected at {result['disconnected_at']}"

    @mcp.tool()
    async def create_checkpoint(
        title: str = "",
        summary: str = "",
        project: str = "",
        checkpoint_type: str = "manual",
    ) -> str:
        """Save a checkpoint of your current work state.

        Use this during important tasks to create a snapshot that can be
        resumed later. Checkpoints are visible in the Agent Dashboard.

        Args:
            title: Short description of what you're working on.
            summary: Detailed summary of progress so far.
            project: Project/workspace name.
            checkpoint_type: "manual" (you decided) or "auto" (periodic).
        """
        tracker = engine.agent_tracker
        if not tracker:
            return "Agent tracker not available."

        if not title:
            title = "Work checkpoint"

        # Collect recent memories as context
        recent = await engine.episodic.search(limit=10)
        memory_ids = [m.id for m in recent]

        result = await tracker.create_checkpoint(
            agent_name="unknown",  # Will be enriched if agent is connected
            title=title,
            summary=summary,
            session_id=None,
            project=project or None,
            checkpoint_type=checkpoint_type,
            memory_ids=memory_ids,
        )

        return (
            f"Checkpoint saved: {result['checkpoint_id'][:8]}...\n"
            f"  Title: {result['title']}\n"
            f"  Time: {result['created_at']}"
        )

    @mcp.tool()
    async def list_agent_activity(limit: int = 20) -> str:
        """List recent agent activity — who connected, when, and their status.

        Shows all agents that have connected to LEVH with timestamps
        and online/offline status.

        Args:
            limit: Maximum number of entries to return (1-100). Default 20.
        """
        tracker = engine.agent_tracker
        if not tracker:
            return "Agent tracker not available."

        activities = await tracker.get_agent_activity(limit=min(max(limit, 1), 100))

        if not activities:
            return "No agent activity recorded yet."

        lines = [f"{len(activities)} agent connections:\n"]
        for a in activities:
            status = "🟢 online" if a.get("online") else "⚪ offline"
            lines.append(
                f"  {a['agent_display']} — {status}\n"
                f"    Connected: {a['connected_at'][:16]}"
                f"{' → ' + a['disconnected_at'][:16] if a.get('disconnected_at') else ''}"
            )
            if a.get("session_id"):
                lines.append(f"    Session: {a['session_id'][:8]}")
            if a.get("project"):
                lines.append(f"    Project: {a['project']}")
            lines.append("")

        return "\n".join(lines)

    @mcp.tool()
    async def list_checkpoints(
        agent_name: str = "",
        project: str = "",
        limit: int = 20,
    ) -> str:
        """List recent checkpoints from agent sessions.

        Args:
            agent_name: Filter by agent name. Empty = all agents.
            project: Filter by project. Empty = all projects.
            limit: Maximum results (1-100). Default 20.
        """
        tracker = engine.agent_tracker
        if not tracker:
            return "Agent tracker not available."

        checkpoints = await tracker.list_checkpoints(
            agent_name=agent_name or None,
            project=project or None,
            limit=min(max(limit, 1), 100),
        )

        if not checkpoints:
            return "No checkpoints found."

        lines = [f"{len(checkpoints)} checkpoints:\n"]
        for cp in checkpoints:
            lines.append(
                f"  [{cp['checkpoint_type']}] {cp['title']}\n"
                f"    Agent: {cp['agent_name']} | {cp['created_at'][:16]}"
            )
            if cp.get("project"):
                lines.append(f"    Project: {cp['project']}")
            lines.append("")

        return "\n".join(lines)

    @mcp.tool()
    async def get_agent_stats() -> str:
        """Get aggregate statistics about agent usage.

        Shows total connections, currently online agents, and per-agent
        breakdowns with session counts.
        """
        tracker = engine.agent_tracker
        if not tracker:
            return "Agent tracker not available."

        stats = await tracker.get_agent_stats()

        lines = ["Agent Statistics:\n"]
        lines.append(f"  Total connections: {stats['total_connections']}")
        lines.append(f"  Currently online: {stats['currently_online']}")

        if stats["online_agents"]:
            lines.append("\n  Online now:")
            for a in stats["online_agents"]:
                lines.append(f"    🟢 {a['display']} ({a['agent_name']})")

        if stats["by_agent"]:
            lines.append("\n  Per-agent breakdown:")
            for a in stats["by_agent"]:
                lines.append(
                    f"    {a['agent_display']}: {a['connection_count']} connections, "
                    f"{a['sessions']} sessions"
                )

        return "\n".join(lines)

    @mcp.tool()
    async def agent_metrics(agent_name: str = "") -> str:
        """Get performance metrics for a specific agent.

        Shows connection history, session count, checkpoint stats,
        and online/offline status for the agent.

        Args:
            agent_name: Agent name (e.g. "claude-code", "cursor").
        """
        tracker = engine.agent_tracker
        if not tracker:
            return "Agent tracker not available."

        if not agent_name:
            return "agent_name is required."

        metrics = await tracker.get_agent_metrics(agent_name)

        lines = [f"Metrics for {metrics['display']} ({metrics['agent_name']}):
"]
        lines.append(f"  Connections: {metrics['connections']}")
        lines.append(f"  Sessions: {metrics['sessions']}")
        lines.append(f"  First seen: {metrics.get('first_seen', 'never')}")
        lines.append(f"  Last seen: {metrics.get('last_seen', 'never')}")
        lines.append(f"  Online: {'Yes' if metrics['currently_online'] else 'No'}")

        if metrics.get("checkpoints"):
            lines.append("
  Checkpoints:")
            for cp_type, cp_data in metrics["checkpoints"].items():
                lines.append(f"    {cp_type}: {cp_data.get('checkpoints', 0)}")

        return "
".join(lines)

    @mcp.tool()
    async def usage_billing() -> str:
        """Get usage billing metrics for all agents.

        Shows connection counts, session counts, checkpoint counts,
        and estimated cost per agent.
        """
        tracker = engine.agent_tracker
        if not tracker:
            return "Agent tracker not available."

        billing = await tracker.get_usage_billing()

        lines = ["Usage Billing:
"]
        summary = billing["summary"]
        lines.append(f"  Total connections: {summary['total_connections']}")
        lines.append(f"  Total sessions: {summary['total_sessions']}")
        lines.append(f"  Total checkpoints: {summary['total_checkpoints']}")

        if billing["by_agent"]:
            lines.append("
  Per-agent:")
            for a in billing["by_agent"]:
                lines.append(
                    f"    {a['agent_name']}: {a['connections']} connections, "
                    f"{a['sessions']} sessions, {a['checkpoints']} checkpoints, "
                    f"~${a['cost_estimate']:.2f}"
                )

        return "
".join(lines)

    @mcp.tool()
    async def project_collaboration(project: str = "") -> str:
        """Get collaboration info for agents working on the same project.

        Shows which agents are active on a project, their online status,
        and shared checkpoints.

        Args:
            project: Project name to check collaboration for.
        """
        tracker = engine.agent_tracker
        if not tracker:
            return "Agent tracker not available."

        if not project:
            return "project is required."

        collab = await tracker.get_project_collaboration(project)

        lines = [f"Collaboration for '{project}':
"]
        lines.append(f"  Collaboration score: {collab['collaboration_score']}")

        if collab["agents"]:
            lines.append("
  Active agents:")
            for a in collab["agents"]:
                status = "🟢 online" if a.get("online") else "⚪ offline"
                lines.append(f"    {a['agent_display']} — {status}")

        if collab["shared_checkpoints"]:
            lines.append("
  Shared checkpoints:")
            for cp in collab["shared_checkpoints"][:5]:
                lines.append(f"    - {cp['title']} ({cp['agent_name']}, {cp['created_at'][:16]})")

        return "
".join(lines)

