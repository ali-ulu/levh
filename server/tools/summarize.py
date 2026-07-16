"""Tool: summarize_session — distill a session's memories into one summary."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    @mcp.tool()
    async def summarize_session(session_id: str) -> str:
        """Distill all memories from a session into one durable summary memory.

        Uses an LLM when OPENAI_API_KEY is configured, otherwise a
        deterministic offline summary. Great to call at the end of a working
        session so the key decisions survive as a single consolidated memory.

        Args:
            session_id: The session to summarize.
        """
        summary = await engine.summarize_session(session_id)
        if not summary:
            return f"Nothing to summarize — session {session_id} has no memories."
        return (
            f"Session summarized into memory {summary.id}:\n\n{summary.content}"
        )
