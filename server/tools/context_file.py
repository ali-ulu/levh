"""Tool 23: generate_context_file — Compile memories into CLAUDE.md / .cursorrules."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    @mcp.tool()
    async def generate_context_file(
        project: str = "",
        style: str = "claude",
    ) -> str:
        """Generate a persistent context file (CLAUDE.md or .cursorrules) from memories.

        Compiles pinned memories, key decisions (importance >= 0.7), and recent
        context into a markdown file that AI clients read natively at session
        start — turning stored memory into always-on context.

        Args:
            project: Only include this project's memories. Empty = all projects.
            style: "claude" for CLAUDE.md format, "cursor" for .cursorrules.
        """
        content = await engine.generate_context_file(
            project=project or None,
            style=style if style in ("claude", "cursor") else "claude",
        )
        filename = "CLAUDE.md" if style != "cursor" else ".cursorrules"
        return f"Generated {filename} content:\n\n{content}"
