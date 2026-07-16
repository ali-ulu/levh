"""Tools: evaluate_admission / admit_memory — the Memory Admission Gate.
Decide what happens to a candidate memory BEFORE it is stored: admit,
redact secrets, hold for review, or reject (near-exact duplicate / too
short). Deterministic, no LLM."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    @mcp.tool()
    async def evaluate_admission(content: str, project: str = "") -> str:
        """Preview the admission gate's verdict for a candidate memory WITHOUT
        storing it: admit / review / redact / reject.

        Args:
            content: The candidate memory text to evaluate.
            project: Optional project filter (used for the duplicate check).
        """
        decision = await engine.evaluate_admission(content, project=project or None)
        action = decision["action"]
        lines = [f"Verdict: {action.upper()}"]
        if decision["reasons"]:
            lines.append(f"Reasons: {', '.join(decision['reasons'])}")
        if decision["redacted"]:
            lines.append(f"Secrets found: {', '.join(decision['secrets'])}")
        if action in ("review", "reject"):
            lines.append(
                "This content would NOT be stored by default "
                "(use admit_memory with force=True to store it anyway)."
            )
        return "\n".join(lines)

    @mcp.tool()
    async def admit_memory(
        content: str,
        importance: float = 0.5,
        project: str = "",
        source: str = "",
        force: bool = False,
    ) -> str:
        """Store a candidate memory through the admission gate: dedupe +
        secret redaction. reject/review verdicts are not stored unless
        force=True; redact verdicts are stored with secrets stripped.

        Args:
            content: The memory text to admit.
            importance: Importance score (0-1). Default 0.5.
            project: Optional project to store under.
            source: Optional source label.
            force: Store even if the gate would reject/hold it. Default False.
        """
        result = await engine.admit_memory(
            content,
            importance=importance,
            project=project or None,
            source=source or None,
            force=force,
        )
        decision = result["decision"]
        action = decision["action"]
        if result["stored"]:
            msg = f"Stored (action: {action.upper()})."
            if decision["redacted"]:
                msg += " Secrets were stripped before storing."
            return msg
        return (
            f"Not stored (action: {action.upper()}). "
            f"Reasons: {', '.join(decision['reasons'])}. "
            "Call again with force=True to store it anyway."
        )
