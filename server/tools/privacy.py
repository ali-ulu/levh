"""Tools: audit_secrets / redact_secrets / purge_memory — hard-delete and
redaction audit (trust). Lets an AI client scan stored memories for
credentials that slipped in before the admission gate existed, strip them in
place, and hard-delete a memory with a verified "fully gone" guarantee.
Deterministic, no LLM."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    @mcp.tool()
    async def audit_secrets() -> str:
        """Read-only scan of stored memories for secrets (credentials,
        tokens, API keys) that slipped in before the admission gate existed,
        or via paths that bypass it. Does not change anything."""
        audit = await engine.audit_secrets()
        if audit["flagged"] == 0:
            return "No secrets found in stored memories."
        lines = [
            f"{audit['flagged']} of {audit['scanned']} memories contain secrets:\n"
        ]
        for item in audit["items"]:
            secrets = ", ".join(item["secret_types"])
            lines.append(f"[{item['id'][:8]}] {secrets} — {item['preview']}")
        lines.append(
            "\nUse redact_secrets(apply=true) to strip secrets from all flagged memories."
        )
        return "\n".join(lines)

    @mcp.tool()
    async def redact_secrets(apply: bool = False) -> str:
        """Bulk-redact secrets across stored memories. Preview by default
        (apply=False); set apply=True to actually rewrite flagged memories.

        Args:
            apply: If True, rewrite every flagged memory in place. If False
                (default), only report what would be redacted.
        """
        result = await engine.redact_all_secrets(dry_run=not apply)
        if not apply:
            if result["flagged"] == 0:
                return "No secrets found — nothing would be redacted."
            return (
                f"{result['flagged']} of {result['scanned']} memories WOULD be "
                f"redacted. Call redact_secrets(apply=true) to apply."
            )
        return (
            f"{result['redacted']} of {result['scanned']} memories WERE redacted "
            f"({result['flagged']} flagged)."
        )

    @mcp.tool()
    async def purge_memory(memory_id: str) -> str:
        """Hard-delete a memory across every layer (short-term, vector
        store, episodic) and verify nothing survives. Pinned memories are
        purged too — this is a deliberate human action, unlike normal decay.

        Args:
            memory_id: The memory to permanently delete.
        """
        result = await engine.purge_memory(memory_id)
        if not result["existed"]:
            return f"No memory {memory_id}."
        if result["purged"]:
            return f"Purged memory {memory_id[:8]} — hard-deleted, fully absent from all layers."
        return (
            f"Purged memory {memory_id[:8]}, but residue remains: {result['residue']}."
        )
