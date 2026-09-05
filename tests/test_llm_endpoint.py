"""One answer to "which model, which URL" — the summarizer and the librarian
must not disagree about it.

They did. Both read SUMMARY_MODEL and both posted to OPENAI_BASE_URL, but with
different defaults, so setting the variable fixed one caller and broke the
other, and leaving it unset sent an OpenRouter model id to api.openai.com.
OPENAI_BASE_URL had the mirror problem: every OpenAI SDK treats it as a base
ending in /v1, this code treated it as the full chat-completions path, so
setting it the conventional way broke both callers silently.
"""

from __future__ import annotations

import pytest

from server.core import llm_endpoint


@pytest.mark.parametrize(
    "configured",
    [
        "https://openrouter.ai/api/v1",                        # base, the convention
        "https://openrouter.ai/api/v1/",                       # base with a slash
        "https://openrouter.ai/api/v1/chat/completions",       # the full path
    ],
)
def test_every_spelling_of_the_base_url_reaches_the_same_endpoint(configured, monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", configured)
    assert llm_endpoint.chat_completions_url() == (
        "https://openrouter.ai/api/v1/chat/completions"
    )


def test_a_local_server_is_reachable_with_the_same_variable(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    assert llm_endpoint.chat_completions_url() == (
        "http://localhost:11434/v1/chat/completions"
    )


def test_the_default_is_the_real_api(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    assert llm_endpoint.chat_completions_url() == (
        "https://api.openai.com/v1/chat/completions"
    )


def test_an_empty_value_falls_back_rather_than_producing_a_bare_path(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "")
    assert llm_endpoint.chat_completions_url().startswith("https://api.openai.com")


def test_the_summarizer_and_the_librarian_read_the_same_model(monkeypatch):
    """The concrete regression: one default was gpt-4o-mini, the other an
    OpenRouter id, and both were reached through the same variable."""
    from server.core import librarian, summarizer

    monkeypatch.setenv("SUMMARY_MODEL", "some/model:free")
    assert llm_endpoint.chat_model() == "some/model:free"

    for module in (summarizer, librarian):
        source = __import__("pathlib").Path(module.__file__).read_text(encoding="utf-8")
        assert 'getenv("SUMMARY_MODEL"' not in source, (
            f"{module.__name__} reintroduced its own model default"
        )
        assert 'getenv("OPENAI_BASE_URL"' not in source, (
            f"{module.__name__} reintroduced its own endpoint default"
        )


def test_the_default_model_is_read_per_call_not_frozen_at_import(monkeypatch):
    monkeypatch.setenv("SUMMARY_MODEL", "first")
    assert llm_endpoint.chat_model() == "first"
    monkeypatch.setenv("SUMMARY_MODEL", "second")
    assert llm_endpoint.chat_model() == "second"
