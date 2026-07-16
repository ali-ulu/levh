"""Session summarization — turn a pile of session memories into one durable,
consolidated memory.

This closes the biggest product gap versus managed memory services (Mem0/Zep):
those auto-distill conversations into facts; LEVH otherwise relies on
explicit `store` calls. `summarize_session` gives the same "auto-capture on
session end" behavior.

Two backends, chosen automatically:
  - **LLM** (OpenAI chat) when ``OPENAI_API_KEY`` is set and mode allows it —
    produces a real prose/bullet summary.
  - **Extractive fallback** otherwise — deterministic, offline, zero-cost:
    keeps the most important / most recent lines. Never raises, so session
    end never fails just because summarization was unavailable.
"""

from __future__ import annotations

import os

import httpx

_SUMMARY_MODEL = os.getenv("SUMMARY_MODEL", "gpt-4o-mini")
_SYSTEM_PROMPT = (
    "You compress a coding session's memories into a durable summary for an "
    "AI assistant's long-term memory. Output 3-8 terse bullet points capturing "
    "decisions, facts, and unresolved threads. No preamble, bullets only."
)


def _extractive_fallback(texts: list[str], max_chars: int = 800) -> str:
    """Offline summary: dedupe, keep order, cap length. Deterministic."""
    seen: set[str] = set()
    kept: list[str] = []
    for t in texts:
        line = t.strip().replace("\n", " ")
        if not line or line in seen:
            continue
        seen.add(line)
        kept.append(f"- {line}")
    body = "\n".join(kept)
    if len(body) > max_chars:
        body = body[:max_chars].rsplit("\n", 1)[0]
    return body


async def summarize_texts(
    texts: list[str],
    mode: str = "auto",
    client: httpx.AsyncClient | None = None,
) -> str:
    """Summarize a list of memory contents into a single consolidated string.

    Args:
        texts: Memory contents to summarize.
        mode: "auto" (LLM if OPENAI_API_KEY, else extractive), "llm", or
            "extractive".
        client: Optional shared httpx client to reuse.
    """
    texts = [t for t in texts if t and t.strip()]
    if not texts:
        return ""

    use_llm = mode == "llm" or (mode == "auto" and os.getenv("OPENAI_API_KEY"))
    if not use_llm:
        return _extractive_fallback(texts)

    joined = "\n".join(f"- {t.strip()}" for t in texts)
    payload = {
        "model": _SUMMARY_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": f"Session memories:\n{joined}"},
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
        return content or _extractive_fallback(texts)
    except Exception:
        # Any LLM failure degrades to the offline summary — session end must
        # never break because summarization was unavailable.
        return _extractive_fallback(texts)
    finally:
        if owns_client:
            await client.aclose()
