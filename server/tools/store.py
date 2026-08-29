"""Tool 1: store_memory — Store a new memory."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    @mcp.tool()
    async def store_memory(
        content: str,
        importance: float = 0.5,
        tags: str = "",
        session_id: str = "",
        project: str = "",
        source: str = "",
        pinned: bool = False,
        memory_type: str = "short_term",
    ) -> str:
        """Store a new memory through the deterministic admission gate.

        Args:
            content: The text content to remember.
            importance: Importance from 0 (low) to 1 (critical). Default 0.5.
            tags: Comma-separated tags (e.g. "bug,api,auth").
            session_id: Optional session to attach this memory to.
            project: Optional project/workspace name (e.g. repo name) to
                namespace this memory under.
            source: Which AI client/tool is storing this (e.g. "claude-code",
                "cursor", "claude-desktop"). Helps trace where memories come from.
            pinned: Pin this memory — pinned memories never decay and always
                appear in generated context files.
            memory_type: "short_term" or "episodic".
        """
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
        result = await engine.admit_memory(
            content=content,
            importance=importance,
            tags=tag_list,
            session_id=session_id or None,
            project=project or None,
            source=source or None,
            pinned=pinned,
            memory_type=memory_type,
        )
        decision = result["decision"]
        if not result["stored"]:
            # A held candidate is not lost, and reporting "not stored" without
            # saying where it went is what made the old message misleading.
            held = (
                f"  Held for review as {result['held_id']} — list it with "
                "GET /api/memories/held, then admit or discard it.\n"
                if result.get("held_id")
                else ""
            )
            return (
                f"Memory not stored (admission: {decision['action']}).\n"
                f"  Reasons: {', '.join(decision['reasons'])}\n"
                f"{held}"
                "Use the admin admit_memory tool with force=true only when an audited override is intended."
            )
        memory = result["memory"]
        suffix = " Secrets were redacted before storage." if decision["redacted"] else ""
        return (
            f"Memory stored successfully (admission: {decision['action']}).{suffix}\n"
            f"  ID: {memory['id']}\n"
            f"  Type: {memory['memory_type']}\n"
            f"  Importance: {memory['importance']}\n"
            f"  Project: {memory.get('project') or 'none'}\n"
            f"  Pinned: {memory['pinned']}\n"
            f"  Tags: {', '.join(memory.get('tags') or []) or 'none'}"
        )
