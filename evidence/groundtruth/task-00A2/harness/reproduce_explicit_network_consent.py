"""TASK-00A2 characterization: ambient OpenAI key and memory transmission.

No real network request can leave this test.  Every AsyncClient.post call is
replaced at the class boundary before a MemoryEngine path is exercised.  The
guard records the attempted URL and synthetic payload, then returns a local
httpx.Response.
"""

from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest

from server.core.memory_engine import MemoryEngine


def _find_repo_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "pyproject.toml").is_file() and (candidate / "server").is_dir():
            return candidate
    raise RuntimeError("repository root not found from audit harness path")


ROOT = _find_repo_root()
EVIDENCE = Path(
    os.environ.get(
        "LEVH_GROUNDTRUTH_EVIDENCE_DIR",
        ROOT / "evidence" / "groundtruth" / "task-00A2",
    )
)
SYNTHETIC_KEY = "sk-task00a2-synthetic-never-valid"


def _ensure_evidence() -> None:
    (EVIDENCE / "stdout").mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "stderr").mkdir(parents=True, exist_ok=True)


def _reset_evidence() -> None:
    _ensure_evidence()
    for relative in (
        "scenarios.jsonl",
        "captured-httpx-posts.jsonl",
        "network-guard.txt",
        "stdout/pytest.txt",
        "stderr/pytest.txt",
    ):
        (EVIDENCE / relative).write_text("", encoding="utf-8")


def _append_jsonl(relative: str, record: dict[str, Any]) -> None:
    _ensure_evidence()
    with (EVIDENCE / relative).open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _append_text(relative: str, text: str) -> None:
    _ensure_evidence()
    with (EVIDENCE / relative).open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


class NetworkGuard:
    """Intercept httpx POST attempts and never delegate to a transport."""

    def __init__(self) -> None:
        self.scenario = "unlabelled"
        self.calls: list[dict[str, Any]] = []
        self.real_network_requests = 0

    async def post(
        self,
        _client: httpx.AsyncClient,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        payload = json or {}
        authorization = (headers or {}).get("Authorization", "")
        record = {
            "scenario": self.scenario,
            "url": str(url),
            "method": "POST",
            "authorization_header_present": bool(authorization),
            "authorization_uses_synthetic_key": authorization
            == f"Bearer {SYNTHETIC_KEY}",
            "content_type": (headers or {}).get("Content-Type"),
            "model": payload.get("model"),
            "messages": payload.get("messages", []),
            "temperature": payload.get("temperature"),
            "extra_keyword_names": sorted(kwargs),
            "transport_delegated": False,
            "real_network_requests": 0,
        }
        self.calls.append(record)
        _append_jsonl("captured-httpx-posts.jsonl", record)

        messages = payload.get("messages", [])
        system_text = " ".join(
            str(message.get("content", ""))
            for message in messages
            if message.get("role") == "system"
        )
        content = (
            "- SYNTHETIC LOCAL SUMMARY RESPONSE"
            if "compress" in system_text.lower()
            else "SYNTHETIC LOCAL ANSWER RESPONSE [1]"
        )
        request = httpx.Request("POST", str(url))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}}]},
            request=request,
        )


def _install_guard(monkeypatch: pytest.MonkeyPatch) -> NetworkGuard:
    guard = NetworkGuard()

    async def guarded_post(
        client: httpx.AsyncClient,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        return await guard.post(
            client, url, headers=headers, json=json, **kwargs
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", guarded_post)
    return guard


async def _new_engine(db_path: Path) -> MemoryEngine:
    engine = MemoryEngine(db_path=str(db_path), embedder_mode="hash")
    await engine.initialize()
    return engine


async def _session_memory(
    engine: MemoryEngine, content: str, session_name: str
) -> tuple[str, str]:
    session = await engine.create_session(session_name)
    memory = await engine.store(
        content=content,
        importance=0.9,
        tags=["groundtruth", "00A2"],
        session_id=session.id,
        project="GT00A2_NETWORK_CONSENT",
        memory_type="episodic",
    )
    return session.id, memory.id


def _payload_contains(calls: list[dict[str, Any]], needle: str) -> bool:
    return needle in json.dumps(calls, ensure_ascii=False)


def _record_scenario(
    *,
    scenario: str,
    engine_path: str,
    key_state: str,
    requested_mode: str,
    embedder_provider: str | None,
    attempted_posts: int,
    payload_contains_memory: bool,
    real_network_requests: int,
    expected: str,
    passed: bool,
    note: str = "",
) -> None:
    _append_jsonl(
        "scenarios.jsonl",
        {
            "scenario": scenario,
            "engine_path": engine_path,
            "key_state": key_state,
            "requested_mode": requested_mode,
            "embedder_provider": embedder_provider,
            "attempted_httpx_posts": attempted_posts,
            "payload_contains_memory": payload_contains_memory,
            "real_network_requests": real_network_requests,
            "expected": expected,
            "passed": passed,
            "note": note,
        },
    )


@pytest.mark.asyncio
async def test_01_no_key_keeps_all_engine_paths_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reset_evidence()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AUTO_SUMMARIZE_SESSIONS", "1")
    guard = _install_guard(monkeypatch)
    memory_text = "GT00A2_NO_KEY_MEMORY_51C7E9 remains entirely offline"
    engine = await _new_engine(tmp_path / "no-key.db")
    try:
        session_id, _ = await _session_memory(engine, memory_text, "00A2 no key")
        provider = engine.embedder.identity()["provider"]

        before = len(guard.calls)
        guard.scenario = "no_key_engine_ask"
        answer = await engine.ask(memory_text, session_id=session_id)
        ask_calls = guard.calls[before:]
        ask_pass = len(ask_calls) == 0 and memory_text in answer["answer"]
        _record_scenario(
            scenario="no_key_engine_ask",
            engine_path="MemoryEngine.ask",
            key_state="absent",
            requested_mode="auto_hardcoded_by_engine",
            embedder_provider=provider,
            attempted_posts=len(ask_calls),
            payload_contains_memory=_payload_contains(ask_calls, memory_text),
            real_network_requests=guard.real_network_requests,
            expected="offline extractive answer and zero POST attempts",
            passed=ask_pass,
        )

        before = len(guard.calls)
        guard.scenario = "no_key_engine_summarize"
        summary = await engine.summarize_session(session_id, store=False)
        summary_calls = guard.calls[before:]
        summary_pass = (
            len(summary_calls) == 0
            and summary is not None
            and memory_text in summary.content
        )
        _record_scenario(
            scenario="no_key_engine_summarize",
            engine_path="MemoryEngine.summarize_session",
            key_state="absent",
            requested_mode="auto_hardcoded_by_engine",
            embedder_provider=provider,
            attempted_posts=len(summary_calls),
            payload_contains_memory=_payload_contains(summary_calls, memory_text),
            real_network_requests=guard.real_network_requests,
            expected="offline extractive summary and zero POST attempts",
            passed=summary_pass,
        )

        before = len(guard.calls)
        guard.scenario = "no_key_engine_end_session_auto_summary"
        ended = await engine.end_session(session_id)
        end_calls = guard.calls[before:]
        end_pass = len(end_calls) == 0 and ended is not None
        _record_scenario(
            scenario="no_key_engine_end_session_auto_summary",
            engine_path="MemoryEngine.end_session -> summarize_session",
            key_state="absent",
            requested_mode="auto_hardcoded_by_engine",
            embedder_provider=provider,
            attempted_posts=len(end_calls),
            payload_contains_memory=_payload_contains(end_calls, memory_text),
            real_network_requests=guard.real_network_requests,
            expected="offline auto-summary and zero POST attempts",
            passed=end_pass,
        )

        assert ask_pass and summary_pass and end_pass
        assert guard.real_network_requests == 0
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_02_ambient_key_activates_outbound_attempts_on_engine_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", SYNTHETIC_KEY)
    monkeypatch.setenv("AUTO_SUMMARIZE_SESSIONS", "1")
    guard = _install_guard(monkeypatch)
    memory_text = "GT00A2_AMBIENT_SECRET_MEMORY_8A4D20 payload sentinel"
    question = "What is GT00A2_AMBIENT_SECRET_MEMORY_8A4D20?"
    engine = await _new_engine(tmp_path / "ambient-key.db")
    try:
        session_id, _ = await _session_memory(
            engine, memory_text, "00A2 ambient key"
        )
        provider = engine.embedder.identity()["provider"]
        assert provider == "hash"

        before = len(guard.calls)
        guard.scenario = "ambient_key_hash_engine_ask"
        answer = await engine.ask(question, session_id=session_id)
        ask_calls = guard.calls[before:]
        ask_pass = (
            len(ask_calls) == 1
            and _payload_contains(ask_calls, memory_text)
            and _payload_contains(ask_calls, question)
            and answer["answer"] == "SYNTHETIC LOCAL ANSWER RESPONSE [1]"
        )
        _record_scenario(
            scenario="ambient_key_hash_engine_ask",
            engine_path="MemoryEngine.ask",
            key_state="ambient_synthetic_present",
            requested_mode="auto_hardcoded_by_engine",
            embedder_provider=provider,
            attempted_posts=len(ask_calls),
            payload_contains_memory=_payload_contains(ask_calls, memory_text),
            real_network_requests=guard.real_network_requests,
            expected="OpenAI POST attempt includes recalled memory content",
            passed=ask_pass,
        )

        before = len(guard.calls)
        guard.scenario = "ambient_key_hash_engine_summarize"
        summary = await engine.summarize_session(session_id, store=False)
        summary_calls = guard.calls[before:]
        summary_pass = (
            len(summary_calls) == 1
            and _payload_contains(summary_calls, memory_text)
            and summary is not None
            and "SYNTHETIC LOCAL SUMMARY RESPONSE" in summary.content
        )
        _record_scenario(
            scenario="ambient_key_hash_engine_summarize",
            engine_path="MemoryEngine.summarize_session",
            key_state="ambient_synthetic_present",
            requested_mode="auto_hardcoded_by_engine",
            embedder_provider=provider,
            attempted_posts=len(summary_calls),
            payload_contains_memory=_payload_contains(summary_calls, memory_text),
            real_network_requests=guard.real_network_requests,
            expected="OpenAI POST attempt includes session memory content",
            passed=summary_pass,
        )

        before = len(guard.calls)
        guard.scenario = "ambient_key_hash_end_session_auto_summary"
        ended = await engine.end_session(session_id)
        end_calls = guard.calls[before:]
        end_pass = (
            len(end_calls) == 1
            and _payload_contains(end_calls, memory_text)
            and ended is not None
        )
        _record_scenario(
            scenario="ambient_key_hash_end_session_auto_summary",
            engine_path="MemoryEngine.end_session -> summarize_session",
            key_state="ambient_synthetic_present",
            requested_mode="auto_hardcoded_by_engine",
            embedder_provider=provider,
            attempted_posts=len(end_calls),
            payload_contains_memory=_payload_contains(end_calls, memory_text),
            real_network_requests=guard.real_network_requests,
            expected="auto-summary OpenAI POST attempt includes session memory content",
            passed=end_pass,
        )

        assert ask_pass and summary_pass and end_pass
        assert len(guard.calls) == 3
        assert guard.real_network_requests == 0
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_03_explicit_extractive_helpers_block_outbound_with_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from server.core.answerer import answer_question
    from server.core.summarizer import summarize_texts

    monkeypatch.setenv("OPENAI_API_KEY", SYNTHETIC_KEY)
    guard = _install_guard(monkeypatch)
    memory_text = "GT00A2_EXTRACTIVE_ONLY_MEMORY_3D9F62 must stay local"
    sources = [
        {
            "n": 1,
            "id": "synthetic-memory-id",
            "content": memory_text,
            "created_at": "2026-07-18T00:00:00+00:00",
            "project": "GT00A2_NETWORK_CONSENT",
        }
    ]

    guard.scenario = "ambient_key_explicit_extractive_answerer"
    answer = await answer_question(
        "What must stay local?", sources, mode="extractive"
    )
    answer_pass = len(guard.calls) == 0 and memory_text in answer
    _record_scenario(
        scenario="ambient_key_explicit_extractive_answerer",
        engine_path="server.core.answerer.answer_question",
        key_state="ambient_synthetic_present",
        requested_mode="extractive",
        embedder_provider=None,
        attempted_posts=0,
        payload_contains_memory=False,
        real_network_requests=guard.real_network_requests,
        expected="explicit extractive answer remains offline",
        passed=answer_pass,
        note="production helper path; MemoryEngine.ask exposes no mode parameter",
    )

    guard.scenario = "ambient_key_explicit_extractive_summarizer"
    summary = await summarize_texts([memory_text], mode="extractive")
    summary_pass = len(guard.calls) == 0 and memory_text in summary
    _record_scenario(
        scenario="ambient_key_explicit_extractive_summarizer",
        engine_path="server.core.summarizer.summarize_texts",
        key_state="ambient_synthetic_present",
        requested_mode="extractive",
        embedder_provider=None,
        attempted_posts=0,
        payload_contains_memory=False,
        real_network_requests=guard.real_network_requests,
        expected="explicit extractive summary remains offline",
        passed=summary_pass,
        note="production helper path; MemoryEngine.summarize_session exposes no mode parameter",
    )

    ask_has_mode = "mode" in inspect.signature(MemoryEngine.ask).parameters
    summarize_has_mode = (
        "mode" in inspect.signature(MemoryEngine.summarize_session).parameters
    )
    surface_pass = not ask_has_mode and not summarize_has_mode
    _record_scenario(
        scenario="engine_explicit_mode_surface",
        engine_path="MemoryEngine.ask + MemoryEngine.summarize_session signatures",
        key_state="not_applicable",
        requested_mode="extractive_requested_but_not_exposed",
        embedder_provider=None,
        attempted_posts=0,
        payload_contains_memory=False,
        real_network_requests=guard.real_network_requests,
        expected="record whether engine exposes explicit mode control",
        passed=surface_pass,
        note=(
            f"ask_has_mode={ask_has_mode}; "
            f"summarize_session_has_mode={summarize_has_mode}"
        ),
    )

    assert answer_pass and summary_pass and surface_pass
    assert guard.real_network_requests == 0


def test_04_network_guard_proves_no_real_request_was_sent() -> None:
    scenario_rows = [
        json.loads(line)
        for line in (EVIDENCE / "scenarios.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    captured_rows = [
        json.loads(line)
        for line in (EVIDENCE / "captured-httpx-posts.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert len(scenario_rows) == 9
    assert len(captured_rows) == 3
    assert all(row["real_network_requests"] == 0 for row in scenario_rows)
    assert all(row["transport_delegated"] is False for row in captured_rows)
    assert all(
        row["url"] == "https://api.openai.com/v1/chat/completions"
        for row in captured_rows
    )
    assert all(row["authorization_uses_synthetic_key"] for row in captured_rows)
    _append_text(
        "network-guard.txt",
        "httpx.AsyncClient.post patched before engine execution\n"
        "captured_outbound_attempts=3\n"
        "delegated_to_httpx_transport=0\n"
        "real_network_requests=0\n"
        "credential=synthetic_nonfunctional\n",
    )
