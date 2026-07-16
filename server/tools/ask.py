"""Tool: ask_memory — ask your memory a question and get a cited answer."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    @mcp.tool()
    async def ask_memory(
        question: str,
        top_k: int = 6,
        project: str = "",
        session_id: str = "",
    ) -> str:
        """Ask your stored memory a natural-language question and get a direct,
        cited answer.

        Unlike recall_memory (which returns a ranked list), this synthesizes an
        answer grounded ONLY in your memories and cites the ones it used with
        [n] markers, so every claim is traceable to a source and a date. Asking
        is read-only — it never reinforces or reshuffles your memories.

        Uses an LLM when OPENAI_API_KEY is configured; otherwise it returns your
        most relevant memories as evidence (fully offline).

        Args:
            question: What you want to know (e.g. "why did we choose SQLite?").
            top_k: How many memories to ground the answer in (1-20). Default 6.
            project: Restrict to one project/workspace. Empty = all.
            session_id: Restrict to one session. Empty = all.
        """
        result = await engine.ask(
            question=question,
            top_k=min(max(top_k, 1), 20),
            project=project or None,
            session_id=session_id or None,
        )
        answer = result["answer"]
        sources = result["sources"]
        if not sources:
            return answer

        lines = [answer, "", "Sources:"]
        for s in sources:
            when = (s.get("created_at") or "")[:10]
            meta = " · ".join(filter(None, [when, s.get("project")]))
            snippet = s["content"][:100] + ("..." if len(s["content"]) > 100 else "")
            lines.append(f"  [{s['n']}] {snippet}" + (f"  ({meta})" if meta else ""))
        return "\n".join(lines)
