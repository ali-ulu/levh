"""Tool 14: export_memories / import_memories — Data portability."""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    @mcp.tool()
    async def export_memories(session_id: str = "") -> str:
        """Export all memories (or a session's memories) as a JSON string.

        Args:
            session_id: Only export memories from this session. Empty = all.
        """
        data = await engine.export_memories(session_id=session_id or None)
        output = json.dumps(data, indent=2, default=str)
        return f"Exported {len(data)} memories.\n\n{output}"

    @mcp.tool()
    async def import_memories(data: str) -> str:
        """Import memories from a JSON string (as exported by export_memories).

        Args:
            data: JSON array of memory objects.
        """
        try:
            parsed = json.loads(data)
            if not isinstance(parsed, list):
                return "Error: data must be a JSON array of memory objects."
        except json.JSONDecodeError as e:
            return f"Error parsing JSON: {e}"

        result = await engine.import_memories_gated(parsed)
        return (
            f"Imported {result['imported']} memories through the admission gate "
            f"(redacted={result['redacted']}, duplicates={result['duplicates']}, "
            f"held={result['held']}, errors={result['errors']})."
        )
