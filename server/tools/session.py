"""Tool 12 & 13: create_session / end_session — Session management."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    @mcp.tool()
    async def create_session(
        name: str = "Untitled Session",
        metadata: str = "",
    ) -> str:
        """Create a new memory session to group related memories.

        Args:
            name: Session name (e.g. "Claude Code Session #1").
            metadata: Optional JSON string with extra metadata.
        """
        import json

        meta = json.loads(metadata) if metadata else {}
        session = await engine.create_session(name=name, metadata=meta)
        return (
            f"Session created.\n"
            f"  ID: {session.id}\n"
            f"  Name: {session.name}\n"
            f"  Status: {session.status}"
        )

    @mcp.tool()
    async def end_session(session_id: str) -> str:
        """End an active session and consolidate its short-term memories.

        Args:
            session_id: ID of the session to end.
        """
        session = await engine.end_session(session_id)
        if not session:
            return f"Session '{session_id}' not found."
        return (
            f"Session ended.\n"
            f"  ID: {session.id}\n"
            f"  Name: {session.name}\n"
            f"  Status: {session.status}"
        )
