"""Where LLM calls go, and with which model — resolved in one place.

There were two answers to this question in the codebase and they disagreed.
``summarizer`` read ``SUMMARY_MODEL`` defaulting to ``gpt-4o-mini``; the
librarian read the *same* variable defaulting to an OpenRouter model id, and
both posted to ``api.openai.com`` unless overridden. Setting one variable
therefore fixed one caller and broke the other, and leaving it unset sent an
OpenRouter model name to OpenAI, which fails.

``OPENAI_BASE_URL`` had the same problem from the other side. Every OpenAI
SDK treats it as a *base* ending in ``/v1``; this code treated it as the full
``/chat/completions`` path. Anyone setting it the conventional way silently
broke both callers. Here the value is accepted either way and normalized.
"""

from __future__ import annotations

import os

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
_CHAT_PATH = "/chat/completions"


def chat_completions_url() -> str:
    """Full chat-completions URL, whichever form ``OPENAI_BASE_URL`` takes.

    Accepts a base (``https://host/v1``), a base with a trailing slash, or the
    full endpoint path — a local Ollama, LM Studio or vLLM server is pointed at
    with the same variable, and each of their docs writes it differently.
    """
    base = (os.getenv("OPENAI_BASE_URL", "") or DEFAULT_BASE_URL).strip().rstrip("/")
    if base.endswith(_CHAT_PATH):
        return base
    return base + _CHAT_PATH


def chat_model() -> str:
    """The model id for summaries and the librarian's chat.

    One variable, one default. ``SUMMARY_MODEL`` keeps its name because it is
    already documented and set in existing environments.
    """
    return (os.getenv("SUMMARY_MODEL", "") or DEFAULT_MODEL).strip()


def api_key() -> str:
    return os.getenv("OPENAI_API_KEY", "").strip()
