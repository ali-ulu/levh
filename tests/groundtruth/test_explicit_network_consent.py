"""Desired invariant for explicit outbound answer/summary consent.

The full payload-capture characterization is retained under
evidence/groundtruth/task-00A2/harness/. No request in this test can reach a
real transport.
"""

from pathlib import Path
from typing import Any

import httpx
import pytest

from server.core.memory_engine import MemoryEngine


@pytest.mark.asyncio
async def test_ambient_key_alone_never_activates_answer_or_summary_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "gt00a5-synthetic-never-valid")
    monkeypatch.setenv("AUTO_SUMMARIZE_SESSIONS", "1")
    # The invariant is about an ambient KEY, so the opt-ins must be absent —
    # otherwise an operator's own ANSWER_MODE/SUMMARY_MODE decides the verdict
    # and the test reports on the machine it runs on, not on the code.
    monkeypatch.delenv("ANSWER_MODE", raising=False)
    monkeypatch.delenv("SUMMARY_MODE", raising=False)
    calls: list[dict[str, Any]] = []

    async def intercepted_post(
        _client: httpx.AsyncClient,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        calls.append({"url": str(url), "payload_present": bool(kwargs.get("json"))})
        request = httpx.Request("POST", str(url))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "local intercepted response"}}]},
            request=request,
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", intercepted_post)
    engine = MemoryEngine(
        db_path=str(tmp_path / "consent.db"),
        embedder_mode="hash",
    )
    await engine.initialize()
    try:
        session = await engine.create_session("GT00A5 consent invariant")
        await engine.store(
            "GT00A5 private memory canary",
            session_id=session.id,
            project="GT00A5_P0_2_INVARIANT",
            memory_type="episodic",
        )
        await engine.ask("What is the private memory?", session_id=session.id)
        await engine.summarize_session(session.id, store=False)
        await engine.end_session(session.id)
        assert calls == []
    finally:
        await engine.shutdown()
