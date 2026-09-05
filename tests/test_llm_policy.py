"""Outbound-LLM consent policy (P0-2).

An ambient ``OPENAI_API_KEY`` must never, on its own, cause memory content to
leave the machine. Enabling that is one explicit environment variable per
feature. These tests pin the decision function and then walk every product path
that can reach a remote model — including the two that run without any direct
user interaction.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from server.core import llm_policy as policy
from server.core.answerer import answer_question
from server.core.memory_engine import MemoryEngine
from server.core.llm_endpoint import chat_completions_url
from server.core.summarizer import summarize_texts

AMBIENT_KEY = "sk-test-never-valid"


@pytest.fixture
def captured_posts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every outbound POST; no request can reach a real transport."""
    calls: list[str] = []

    async def intercepted_post(_self: httpx.AsyncClient, url: str, **kwargs: Any):
        calls.append(str(url))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "intercepted"}}]},
            request=httpx.Request("POST", str(url)),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", intercepted_post)
    return calls


# ── the decision function ────────────────────────────────────────────


def test_ambient_key_alone_does_not_enable_either_feature(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", AMBIENT_KEY)
    monkeypatch.delenv("ANSWER_MODE", raising=False)
    monkeypatch.delenv("SUMMARY_MODE", raising=False)
    assert policy.use_llm("auto", policy.ANSWER_FEATURE) is False
    assert policy.use_llm("auto", policy.SUMMARY_FEATURE) is False


@pytest.mark.parametrize("value", ["llm", "openai", "on", "true", "1", "yes", "LLM"])
def test_opt_in_values_enable_the_feature(monkeypatch, value):
    monkeypatch.setenv("OPENAI_API_KEY", AMBIENT_KEY)
    monkeypatch.setenv("ANSWER_MODE", value)
    assert policy.use_llm("auto", policy.ANSWER_FEATURE) is True


@pytest.mark.parametrize("value", ["", "extractive", "local", "off", "false", "no"])
def test_non_opt_in_values_keep_the_feature_offline(monkeypatch, value):
    monkeypatch.setenv("OPENAI_API_KEY", AMBIENT_KEY)
    monkeypatch.setenv("ANSWER_MODE", value)
    assert policy.use_llm("auto", policy.ANSWER_FEATURE) is False


def test_opt_in_per_feature_does_not_leak_across_features(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", AMBIENT_KEY)
    monkeypatch.setenv("ANSWER_MODE", "llm")
    monkeypatch.delenv("SUMMARY_MODE", raising=False)
    assert policy.use_llm("auto", policy.ANSWER_FEATURE) is True
    assert policy.use_llm("auto", policy.SUMMARY_FEATURE) is False


def test_opt_in_without_a_credential_stays_offline(monkeypatch):
    """Enabling the feature with no key degrades instead of failing a session."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("SUMMARY_MODE", "llm")
    assert policy.use_llm("auto", policy.SUMMARY_FEATURE) is False
    assert policy.use_llm("llm", policy.SUMMARY_FEATURE) is False


def test_explicit_extractive_always_wins(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", AMBIENT_KEY)
    monkeypatch.setenv("SUMMARY_MODE", "llm")
    assert policy.use_llm("extractive", policy.SUMMARY_FEATURE) is False


def test_outbound_status_reports_posture_without_leaking_the_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", AMBIENT_KEY)
    monkeypatch.delenv("ANSWER_MODE", raising=False)
    monkeypatch.delenv("SUMMARY_MODE", raising=False)
    status = policy.outbound_status()
    assert status["openai_credential_present"] is True
    assert status["outbound_llm_enabled"] is False
    assert status["answer_backend"] == policy.EXTRACTIVE
    assert AMBIENT_KEY not in str(status)


# ── every product path that can reach a remote model ─────────────────


@pytest.mark.asyncio
async def test_answer_and_summary_helpers_stay_offline(monkeypatch, captured_posts):
    monkeypatch.setenv("OPENAI_API_KEY", AMBIENT_KEY)
    monkeypatch.delenv("ANSWER_MODE", raising=False)
    monkeypatch.delenv("SUMMARY_MODE", raising=False)

    await answer_question("q", [{"n": 1, "content": "canary"}])
    await summarize_texts(["canary one", "canary two"])
    assert captured_posts == []


@pytest.mark.asyncio
async def test_consolidation_stays_offline(tmp_path, monkeypatch, captured_posts):
    """Consolidation summarizes in the background, with no user in the loop —
    the worst place for a silent upload."""
    monkeypatch.setenv("OPENAI_API_KEY", AMBIENT_KEY)
    monkeypatch.delenv("SUMMARY_MODE", raising=False)
    engine = MemoryEngine(db_path=str(tmp_path / "c.db"), embedder_mode="hash")
    await engine.initialize()
    try:
        for i in range(4):
            await engine.store(
                f"The deploy pipeline canary detail number {i}",
                memory_type="episodic",
                project="p0_2",
            )
        await engine.consolidate_memories(
            similarity_threshold=0.1, min_age_days=0, min_cluster_size=2,
            project="p0_2", dry_run=False,
        )
        assert captured_posts == []
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_transcript_connector_stays_offline(tmp_path, monkeypatch, captured_posts):
    """Transcript ingest summarizes each meeting on import."""
    monkeypatch.setenv("OPENAI_API_KEY", AMBIENT_KEY)
    monkeypatch.delenv("SUMMARY_MODE", raising=False)
    vtt = tmp_path / "meeting.vtt"
    vtt.write_text(
        "WEBVTT\n\n00:00:01.000 --> 00:00:04.000\n"
        "<v Dana>We decided the canary rollout ships on Friday.\n\n"
        "00:00:05.000 --> 00:00:08.000\n"
        "<v Sam>Agreed, the canary budget is approved.\n",
        encoding="utf-8",
    )
    from server.connectors.transcript import TranscriptConnector

    connector = TranscriptConnector()
    assert await connector.connect({"transcript_path": str(vtt)}) is True
    items = await connector.fetch()
    assert items, "transcript connector produced no memories"
    assert captured_posts == []


@pytest.mark.asyncio
async def test_explicit_opt_in_does_reach_the_model(monkeypatch, captured_posts):
    """The opt-in has to actually work, or the feature is merely broken."""
    monkeypatch.setenv("OPENAI_API_KEY", AMBIENT_KEY)
    monkeypatch.setenv("SUMMARY_MODE", "llm")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    await summarize_texts(["canary one", "canary two"])
    assert captured_posts == ["https://api.openai.com/v1/chat/completions"]


@pytest.mark.asyncio
async def test_a_local_endpoint_replaces_the_openai_one(monkeypatch, captured_posts):
    """OPENAI_BASE_URL is how a summary can stay on the machine — it must be used."""
    monkeypatch.setenv("OPENAI_API_KEY", AMBIENT_KEY)
    monkeypatch.setenv("SUMMARY_MODE", "llm")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    await summarize_texts(["canary one", "canary two"])
    assert captured_posts == ["http://localhost:11434/v1/chat/completions"]
    assert captured_posts == [chat_completions_url()]
