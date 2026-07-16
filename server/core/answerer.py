"""Ask-your-life — answer a natural-language question from stored memories.

This is the flagship "second brain" capability: instead of just returning a
ranked list of memories (recall), it synthesizes a direct answer and cites the
exact memories it drew from, so every claim is traceable back to a source and a
date. This is what turns a memory *store* into a memory you can *ask*.

Two backends, chosen automatically (same philosophy as summarizer):
  - **LLM** (OpenAI chat) when ``OPENAI_API_KEY`` is set — a real synthesized
    answer that only uses the provided memories, with inline [n] citations.
  - **Extractive fallback** otherwise — deterministic, offline, zero-cost:
    returns the most relevant memories as an evidence list. Never raises.
"""

from __future__ import annotations

import os

import httpx

_ANSWER_MODEL = os.getenv("ANSWER_MODEL", "gpt-4o-mini")
_SYSTEM_PROMPT = (
    "You are the user's personal memory. Answer the question using ONLY the "
    "numbered memories provided — never invent facts. Cite the memories you "
    "use with inline markers like [1], [2]. If the memories do not contain the "
    "answer, say so plainly. Be concise and direct; the user is asking about "
    "their own life and work."
)


def _extractive_fallback(question: str, sources: list[dict]) -> str:
    """Offline answer: no LLM, so present the top evidence the user can read.
    Deterministic and honest — it never pretends to synthesize."""
    if not sources:
        return (
            "I don't have any memories matching that question yet. "
            "Store some memories or connect a source, then ask again."
        )
    lines = [
        "I can't synthesize an answer without an LLM configured, but here are "
        "your most relevant memories (set OPENAI_API_KEY or run Ollama for a "
        "written answer):",
        "",
    ]
    for s in sources:
        when = (s.get("created_at") or "")[:10]
        meta = " · ".join(filter(None, [when, s.get("project")]))
        suffix = f"  ({meta})" if meta else ""
        lines.append(f"[{s['n']}] {s['content'].strip()}{suffix}")
    return "\n".join(lines)


async def answer_question(
    question: str,
    sources: list[dict],
    mode: str = "auto",
    client: httpx.AsyncClient | None = None,
) -> str:
    """Synthesize an answer to ``question`` from numbered ``sources``.

    Args:
        question: The user's natural-language question.
        sources: List of dicts with at least ``n`` (citation index) and
            ``content``; ``created_at``/``project`` used for context.
        mode: "auto" (LLM if key, else extractive), "llm", or "extractive".
        client: Optional shared httpx client.
    """
    if not sources:
        return _extractive_fallback(question, sources)

    use_llm = mode == "llm" or (mode == "auto" and os.getenv("OPENAI_API_KEY"))
    if not use_llm:
        return _extractive_fallback(question, sources)

    numbered = "\n".join(
        f"[{s['n']}] ({(s.get('created_at') or '')[:10]}) {s['content'].strip()}"
        for s in sources
    )
    payload = {
        "model": _ANSWER_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Memories:\n{numbered}\n\nQuestion: {question}",
            },
        ],
        "temperature": 0.2,
    }
    headers = {
        "Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}",
        "Content-Type": "application/json",
    }
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=30.0)
    try:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()
        return content or _extractive_fallback(question, sources)
    except Exception:
        # Any LLM failure degrades to the offline evidence list — asking your
        # memory must never hard-fail just because the LLM was unavailable.
        return _extractive_fallback(question, sources)
    finally:
        if owns_client:
            await client.aclose()
