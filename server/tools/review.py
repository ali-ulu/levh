"""Tools: list_review_memories / review_memory — spaced-repetition review of
fading memories. Surfaces memories losing strength and lets a human decide to
keep, reinforce, weaken, pin, forget, or snooze them — closing the memory
lifecycle loop (store → recall → decay → review → reinforce/weaken/forget).
Deterministic, no LLM."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine

_ACTIONS = "keep|reinforce|weaken|pin|forget|snooze"


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    @mcp.tool()
    async def list_review_memories(
        threshold: float = 0.5, limit: int = 20, project: str = ""
    ) -> str:
        """List memories due for spaced-repetition review — fading (predicted
        retention below ``threshold``), not pinned, not snoozed. Each is shown
        with the decay context you need to decide what to do with it.

        Args:
            threshold: Retention below which a memory is "due". Default 0.5.
            limit: Max memories to list. Default 20.
            project: Optional project filter.
        """
        items = await engine.review_queue(
            threshold=threshold, project=project or None, limit=limit
        )
        if not items:
            return "No memories due for review."
        lines = [f"{len(items)} memories due for review:\n"]
        for i, it in enumerate(items, 1):
            lines.append(
                f"{i}. [{it['id'][:8]}] {it['content']}  "
                f"(retention {it['retention']}, {it['recall_count']} recalls) — {it['reason']}"
            )
        lines.append(
            f"\nUse review_memory(memory_id, action) with action = {_ACTIONS}."
        )
        return "\n".join(lines)

    @mcp.tool()
    async def review_memory(
        memory_id: str, action: str, snooze_days: int = 7, reason: str = ""
    ) -> str:
        """Apply a review decision to a memory, recorded auditably in its
        metadata. Pinned memories are never auto-forgotten.

        Args:
            memory_id: The memory to review.
            action: One of keep, reinforce, weaken, pin, forget, snooze.
                keep = reset decay clock (mild); reinforce = strong boost;
                weaken = fade faster; pin = never decays; forget = remove;
                snooze = push next review out by ``snooze_days``.
            snooze_days: Days to snooze (only for action=snooze). Default 7.
            reason: Optional note stored in the review history.
        """
        try:
            result = await engine.apply_review(
                memory_id, action, snooze_days=snooze_days, reason=reason
            )
        except ValueError as exc:
            return f"Invalid review: {exc}"
        if not result.get("ok"):
            return f"No memory {memory_id} to review."
        if action == "forget":
            return f"Forgot memory {memory_id[:8]} (removed from active recall)."
        if action == "snooze":
            return (
                f"Snoozed memory {memory_id[:8]} — next review at "
                f"{result.get('review_due_at')}."
            )
        return (
            f"Applied '{action}' to memory {memory_id[:8]} "
            f"(reviewed {result.get('review_count')}x)."
        )
