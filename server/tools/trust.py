"""Tools: memory_trust / recompute_trust_scores / list_low_trust_memories —
the provenance/trust score surface. A deterministic, explainable *reliability*
signal (source, corroboration, review lifecycle, recency, risk flags) — NOT
truth, and NEVER used to alter H-score / recall ranking. No LLM, no network."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    @mcp.tool()
    async def memory_trust(memory_id: str) -> str:
        """Show the provenance/trust breakdown for a memory: confidence,
        label, each scoring component, and a human-readable explanation.
        Computes and caches the score on demand if it hasn't been scored yet.

        Args:
            memory_id: The memory to inspect.
        """
        result = await engine.get_trust(memory_id)
        if result is None:
            return f"No memory {memory_id}."
        c = result["components"]
        lines = [
            f"Trust for memory {result['memory_id'][:8]}: "
            f"confidence {result['confidence']} ({result['label']})",
            "Components:",
            f"  source_score: {c['source_score']}",
            f"  corroboration_score: {c['corroboration_score']}",
            f"  review_score: {c['review_score']}",
            f"  recency_score: {c['recency_score']}",
            f"  risk_penalty: {c['risk_penalty']}",
            "Explanation:",
        ]
        for line in result.get("explanation", []):
            lines.append(f"  - {line}")
        return "\n".join(lines)

    @mcp.tool()
    async def recompute_trust_scores() -> str:
        """Recompute and persist the provenance/trust score for every stored
        memory. Deterministic, no LLM. Does not affect H-score / recall
        ranking — trust is an independent, explainable signal."""
        result = await engine.recompute_trust_scores()
        by_label = result.get("by_label", {})
        dist = ", ".join(f"{label}={count}" for label, count in sorted(by_label.items()))
        return f"Scored {result['scored']} memories. By label: {dist or 'none'}."

    @mcp.tool()
    async def list_low_trust_memories(threshold: float = 0.4, limit: int = 20) -> str:
        """List stored memories whose provenance/trust confidence is below
        ``threshold``, least confident first. Run recompute_trust_scores
        first to populate the scores.

        Args:
            threshold: Confidence below which a memory is "low trust". Default 0.4.
            limit: Max memories to list. Default 20.
        """
        items = await engine.list_low_trust(threshold=threshold, limit=limit)
        if not items:
            return "No low-trust memories (run recompute_trust_scores first)."
        lines = [f"{len(items)} low-trust memories (threshold {threshold}):\n"]
        for i, it in enumerate(items, 1):
            line = f"{i}. [{it['label']}] {it['confidence']} — {it['memory_id'][:8]}"
            explanation = it.get("explanation") or []
            if explanation:
                line += f" ({explanation[0]})"
            lines.append(line)
        return "\n".join(lines)
