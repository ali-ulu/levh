"""Librarian watchdog: finding storage, action guardrails, HTTP surface.

The watchdog scans in a worker thread (`asyncio.to_thread`), so every write it
makes crosses a thread boundary back into the server's event loop. That is the
part that kept breaking, so it is the part these tests pin down.
"""

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
        librarian.set_owner_loop(None)
        if os.path.exists(db_path):
            os.unlink(db_path)


@pytest_asyncio.fixture
async def client(engine):
    from server.api import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ── Finding storage across the thread boundary ────────────────────────


@pytest.mark.asyncio
async def test_store_finding_from_worker_thread_reaches_the_database(engine):
    """The watchdog's own writes must land, not vanish into a dead loop."""
    librarian.set_owner_loop(asyncio.get_running_loop())

    await asyncio.to_thread(librarian._store_finding, "LIBRARIAN TEST bulgusu 1")

    rows = await engine.list_memories(limit=50)
    contents = [m.content if hasattr(m, "content") else m["content"] for m in rows]
    assert any("LIBRARIAN TEST bulgusu 1" in c for c in contents)


@pytest.mark.asyncio
async def test_store_finding_inside_the_loop_reaches_the_database(engine):
    """Called from async code directly (routes), the write must still land."""
    librarian.set_owner_loop(asyncio.get_running_loop())

    librarian._store_finding("LIBRARIAN TEST bulgusu 2")
    await asyncio.sleep(0.2)  # scheduled as a task on this loop

    rows = await engine.list_memories(limit=50)
    contents = [m.content if hasattr(m, "content") else m["content"] for m in rows]
    assert any("LIBRARIAN TEST bulgusu 2" in c for c in contents)


@pytest.mark.asyncio
async def test_scan_stores_a_finding(engine):
    librarian.set_owner_loop(asyncio.get_running_loop())

    report = await asyncio.to_thread(librarian.scan, True)

    assert "agents" in report and "activity" in report
    rows = await engine.list_memories(limit=50)
    contents = [m.content if hasattr(m, "content") else m["content"] for m in rows]
    assert any("LEVH LIBRARIAN" in c for c in contents)


@pytest.mark.asyncio
async def test_activity_report_reads_the_engine_database(engine):
    """The report must describe the DB the engine writes to, not another file."""
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


# ── Destructive-command guardrail ─────────────────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        r"Remove-Item -Recurse C:\ ",
        r"Remove-Item -Recurse -Force C:\Users",
        r"remove-item -recurse c:\\",
        r"del /f C:\important",
        r"del /q D:\data",
        "format c:",
        r"rd /s /q C:\Windows",
        r"reg delete HKLM\Software",
        "diskpart",
        "cipher /w:C",
        "bcdedit /set safeboot minimal",
    ],
)
def test_destructive_commands_are_blocked(command):
    assert librarian._BLOCKED_RE.search(command), f"not blocked: {command!r}"


@pytest.mark.parametrize(
    "command",
    [
        "Get-Process",
        "python -m pytest -q",
        r"Get-Content C:\Users\x\.codex\config.toml",
        "Remove-Item .\\build\\tmp.txt",
    ],
)
def test_ordinary_commands_are_not_blocked(command):
    assert not librarian._BLOCKED_RE.search(command), f"wrongly blocked: {command!r}"


def test_shell_can_be_switched_off(monkeypatch):
    """With the switch off, nothing is spawned no matter how benign the command."""
    monkeypatch.setenv("LEVH_LIBRARIAN_SHELL", "0")

    def _boom(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("powershell was spawned while the shell was off")

    monkeypatch.setattr(librarian.subprocess, "run", _boom)

    result = librarian.run_shell("Get-Process")
    assert result["ok"] is False
    assert "LEVH_LIBRARIAN_SHELL" in result["msg"]


def test_run_shell_refuses_a_destructive_command(monkeypatch):
    """The guard must trip BEFORE powershell is ever spawned."""
    calls = []

    def _boom(*args, **kwargs):  # pragma: no cover - must not run
        calls.append(args)
        raise AssertionError("powershell was spawned for a blocked command")

    monkeypatch.setattr(librarian.subprocess, "run", _boom)
    monkeypatch.setattr(librarian, "_store_finding", lambda _content: None)

    result = librarian.run_shell(r"Remove-Item -Recurse C:\ ")

    assert result["ok"] is False
    assert not calls


# ── Action parsing ────────────────────────────────────────────────────


def test_split_reply_and_action_reads_a_fenced_block():
    text = 'Bakiyorum.\n```json\n{"action": {"type": "none"}, "reply": "ok"}\n```'
    reply, action = librarian._split_reply_and_action(text)
    assert action == {"type": "none"}
    assert "Bakiyorum." in reply


def test_split_reply_and_action_reads_a_bare_object():
    text = '{"action": {"type": "shell", "command": "Get-Process"}, "reply": "ok"}'
    _reply, action = librarian._split_reply_and_action(text)
    assert action == {"type": "shell", "command": "Get-Process"}


def test_split_reply_and_action_uses_the_json_reply_when_there_is_no_prose():
    """The prompt asks for the explanation inside the JSON — read it from there."""
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


# ── Chat ──────────────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self._content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"choices": [{"message": {"content": self._content}}]}


class _FakeAsyncClient:
    """Records every payload the librarian sends to the LLM."""

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


# ── HTTP surface ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_endpoint_reports_agents_and_activity(client):
    r = await client.get("/api/librarian/status")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["agents"], list) and body["agents"]
    assert "activity" in body


@pytest.mark.asyncio
async def test_widget_is_served_as_javascript(client):
    """A `<script src>` served as text/html is refused by nosniff clients."""
    r = await client.get("/librarian.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]
    assert "__levhLibrarian" in r.text


@pytest.mark.asyncio
async def test_html_pages_get_the_widget_injected(client):
    r = await client.get("/librarian")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert '<script src="/librarian.js"></script>' in r.text


@pytest.mark.asyncio
async def test_injection_leaves_no_stale_content_length(client):
    """Rewriting the body invalidates the length the upstream response set."""
    r = await client.get("/librarian")
    declared = r.headers.get("content-length")
    if declared is not None:
        assert int(declared) == len(r.content)


@pytest.mark.asyncio
async def test_json_responses_are_left_alone(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    assert "librarian.js" not in r.text


@pytest.mark.asyncio
async def test_chat_without_an_llm_key_returns_context_not_an_error(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    r = await client.post("/api/librarian/chat", json={"question": "durum ne?"})
    assert r.status_code == 200
    body = r.json()
    assert body["backend"] == "offline"
    assert body["actions"] == []
    assert "CONTEXT:" in body["answer"]
