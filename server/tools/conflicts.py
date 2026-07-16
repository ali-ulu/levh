"""Tools: detect_conflict_candidates / list_conflict_candidates /
review_conflict_candidate — deterministic conflict-CANDIDATE review over
captured memory. Two memories are flagged only when they (a) share an entity
and (b) show an opposing surface pattern (antonym / negation /
attribute-value). This is a review signal for a human, never a verdict, and
it never auto-deletes a memory. No LLM, no network."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine

_ACTIONS = "dismiss|confirm|resolve_keep_a|resolve_keep_b|mark_both_valid|human_review"


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    @mcp.tool()
    async def detect_conflict_candidates() -> str:
        """Scan stored memories for conflict CANDIDATES — pairs that share an
        entity and show an opposing surface pattern (antonym, negation, or the
        same attribute with different values). Deterministic, offline, no
        LLM. Idempotent: never resets a candidate that has already been
        reviewed. Follow up with list_conflict_candidates to see what was
        found, then review_conflict_candidate to act on one."""
        result = await engine.detect_conflict_candidates()
        return (
            f"Detected {result['new_candidates']} new conflict candidate(s) "
            f"out of {result['pairs_examined']} entity-sharing pair(s) examined "
            f"({result['open_total']} open total). These are candidates for "
            "human review, not verdicts — nothing was deleted or judged true/false."
        )

    @mcp.tool()
    async def list_conflict_candidates(status: str = "open", limit: int = 20) -> str:
        """List conflict candidates, most recent first.

        Args:
            status: Filter by status — one of open, confirmed, dismissed,
                resolved. Empty string lists every status. Default "open".
            limit: Max candidates to return. Default 20.
        """
        items = await engine.list_conflict_candidates(
            status=status or None, limit=min(max(limit, 1), 1000)
        )
        if not items:
            return "No conflict candidates."
        lines = [f"{len(items)} conflict candidate(s):\n"]
        for it in items:
            expl = it.get("explanation") or {}
            detail = expl.get("detail", "")
            a_preview = expl.get("a_preview", "")
            b_preview = expl.get("b_preview", "")
            lines.append(
                f"[{it['id']}] {it['signal_type']} ({detail}) — "
                f"confidence {it['confidence']}, status {it['status']}"
            )
            lines.append(f"  A: {a_preview}")
            lines.append(f"  B: {b_preview}")
            if it.get("shared_entities"):
                lines.append(f"  shared entities: {', '.join(it['shared_entities'])}")
            lines.append("")
        lines.append(
            f"Use review_conflict_candidate(conflict_id, action) with action = {_ACTIONS}."
        )
        return "\n".join(lines)

    @mcp.tool()
    async def review_conflict_candidate(conflict_id: str, action: str) -> str:
        """Apply a human review decision to a conflict candidate. Never
        deletes a memory — resolve_keep_a/resolve_keep_b only weakens the
        memory that was NOT kept (an explicit human choice), the other
        actions just record the decision auditably.

        Args:
            conflict_id: The candidate id (from list_conflict_candidates).
            action: One of dismiss, confirm, resolve_keep_a, resolve_keep_b,
                mark_both_valid, human_review.
        """
        try:
            result = await engine.review_conflict_candidate(conflict_id, action)
        except ValueError as exc:
            return f"Invalid review: {exc}"
        if not result.get("ok"):
            return f"No conflict {conflict_id}."
        conflict = result.get("conflict", {})
        return (
            f"Applied '{action}' to conflict {conflict_id} — "
            f"status is now '{conflict.get('status')}'."
        )
