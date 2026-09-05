"""The librarian's reading, its answers, and the memory of a conversation.

What it may *do* is pinned in test_librarian_authority.py, and the widget it
serves in test_widget_injection.py. This file covers the rest: that the scan
describes the database the engine actually uses, and that a chat turn survives
the round trip from model output to something a person can read.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["EMBEDDER_MODE"] = "hash"

from server.core import engine_provider, librarian  # noqa: E402
from server.core.memory_engine import MemoryEngine  # noqa: E402


@pytest_asyncio.fixture
async def engine():
    """Point BOTH the api module and engine_provider at a throwaway DB."""
    import server.api as api_mod

    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    eng = MemoryEngine(db_path=db_path, embedder_mode="hash", short_term_max=50)
    await eng.initialize()

    prev_api_engine = api_mod._engine
    api_mod._engine = eng
    api_mod._initialized = True
    engine_provider.set_engine(eng)
    try:
        yield eng
    finally:
        await eng.shutdown()
        engine_provider.set_engine(None)
        api_mod._engine = prev_api_engine
        api_mod._initialized = prev_api_engine is not None
        if os.path.exists(db_path):
            os.unlink(db_path)


@pytest_asyncio.fixture
async def client(engine):
    from server.api import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ── What the scan reads ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_activity_report_reads_the_engine_database(engine):
    """The report must describe the DB the engine writes to, not another file.

    Guessing the path (``~/AppData/Local/stackmemory.db``) meant the report
    could open an empty file and conclude that every agent had gone silent.
    """
    await engine.admit_memory(
        content="Aktivite raporu icin ornek kayit — codex tarafindan yazildi.",
        importance=0.6,
        source="codex",
        memory_type="short_term",
    )

    activity = await asyncio.to_thread(librarian._activity_report)

    assert "error" not in activity, activity
    assert activity["memories_per_source"].get("codex") == 1
    assert "codex" not in activity["silent_agents"]


@pytest.mark.asyncio
async def test_scan_reports_agents_and_activity_without_writing(engine):
    report = await asyncio.to_thread(librarian.scan)

    assert isinstance(report["agents"], list) and report["agents"]
    assert "activity" in report and "at" in report
    assert await engine.list_memories(limit=10) == []


@pytest.mark.asyncio
async def test_status_endpoint_reports_agents_and_activity(client):
    r = await client.get("/api/librarian/status")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["agents"], list) and body["agents"]
    assert "activity" in body


# ── Reading the model's answer ────────────────────────────────────────


def test_split_reply_and_action_reads_a_fenced_block():
    text = 'Bakiyorum.\n```json\n{"action": {"type": "none"}, "reply": "ok"}\n```'
    reply, action = librarian._split_reply_and_action(text)
    assert action == {"type": "none"}
    assert "Bakiyorum." in reply


def test_split_reply_and_action_reads_a_bare_object():
    text = '{"action": {"type": "report_finding"}, "reply": "ok"}'
    _reply, action = librarian._split_reply_and_action(text)
    assert action == {"type": "report_finding"}


def test_split_reply_and_action_uses_the_json_reply_when_there_is_no_prose():
    """The prompt asks for the explanation inside the JSON — read it from there.

    Dropping it left the chat window showing an empty bubble for exactly the
    answers that followed the instructions.
    """
    text = '{"action": {"type": "none"}, "reply": "Her sey bagli gorunuyor."}'
    reply, action = librarian._split_reply_and_action(text)
    assert reply == "Her sey bagli gorunuyor."
    assert action == {"type": "none"}


def test_split_reply_and_action_uses_the_json_reply_in_a_fenced_block():
    text = '```json\n{"action": {"type": "none"}, "reply": "Bagli."}\n```'
    reply, _action = librarian._split_reply_and_action(text)
    assert reply == "Bagli."


def test_split_reply_and_action_passes_plain_text_through():
    reply, action = librarian._split_reply_and_action("Her sey yolunda.")
    assert action is None
    assert reply == "Her sey yolunda."


# ── The conversation ──────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self._content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"choices": [{"message": {"content": self._content}}]}


class _FakeAsyncClient:
    """Records every payload the librarian sends to the model."""

    payloads: list[dict] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *args) -> bool:
        return False

    async def post(self, _url, headers=None, json=None):
        _FakeAsyncClient.payloads.append(json)
        return _FakeResponse("Buradayim.")


@pytest.mark.asyncio
async def test_chat_carries_the_previous_turns_to_the_llm(monkeypatch):
    """A follow-up like 'evet, yap' is meaningless without the turn before it."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(librarian, "_context_block", lambda: "CONTEXT: {}")
    monkeypatch.setattr(librarian.httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.payloads.clear()
    librarian._CHAT_HISTORY.clear()

    await librarian.chat("ilk soru")
    await librarian.chat("ikinci soru")

    second = _FakeAsyncClient.payloads[-1]["messages"]
    assert any(m["content"] == "ilk soru" for m in second), second
    assert any(m["role"] == "assistant" and "Buradayim." in m["content"] for m in second)


@pytest.mark.asyncio
async def test_chat_never_answers_with_an_empty_bubble(monkeypatch):
    """A model that replies with an action-only JSON must still say something."""

    class _EmptyReply(_FakeAsyncClient):
        async def post(self, _url, headers=None, json=None):
            return _FakeResponse('{"action": {"type": "none"}}')

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(librarian, "_context_block", lambda: "CONTEXT: {}")
    monkeypatch.setattr(librarian.httpx, "AsyncClient", _EmptyReply)
    librarian._CHAT_HISTORY.clear()

    out = await librarian.chat("durum?")
    assert out["answer"].strip()


@pytest.mark.asyncio
async def test_chat_without_an_llm_key_returns_context_not_an_error(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    r = await client.post("/api/librarian/chat", json={"question": "durum ne?"})
    assert r.status_code == 200
    body = r.json()
    assert body["backend"] == "offline"
    assert body["actions"] == []
    assert "CONTEXT:" in body["answer"]
