"""Tests for the Transcript (.vtt/.srt/.txt) connector — Phase 1 capture #3.
Offline: sample transcripts, extractive summary (no OPENAI_API_KEY)."""

import os
import sys
import tempfile

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["EMBEDDER_MODE"] = "hash"
os.environ.pop("OPENAI_API_KEY", None)  # force extractive summary path

from server.connectors import get_connector, list_connectors
from server.connectors.transcript import TranscriptConnector, parse_transcript
from server.core.memory_engine import MemoryEngine

VTT = """WEBVTT

NOTE recorded by Zoom

1
00:00:01.000 --> 00:00:04.000
<v Alice>Welcome everyone, let's review the Q3 roadmap.

2
00:00:04.500 --> 00:00:08.000
<v Bob>I think we should prioritize the mobile app.

3
00:00:08.500 --> 00:00:11.000
<v Alice>Agreed. Bob will own the mobile milestone.
"""

SRT = """1
00:00:01,000 --> 00:00:03,000
Dana: The pricing needs a 20% discount tier.

2
00:00:03,500 --> 00:00:06,000
You: Okay, I'll draft that by Friday.
"""

TXT = """Alice: Kickoff for the migration project.
Bob: We move to Postgres next sprint.
Alice: I'll send the schema tonight.
"""


def _write(text: str, suffix: str) -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return path


# ── parser (unit) ──────────────────────────────────────────────────


def test_parse_vtt_speakers_and_lines():
    p = parse_transcript(VTT)
    assert p["speakers"] == ["Alice", "Bob"]
    assert any("mobile app" in ln for ln in p["lines"])
    assert "WEBVTT" not in p["text"]
    assert "-->" not in p["text"]  # cue lines stripped


def test_parse_srt_speaker_prefix():
    p = parse_transcript(SRT)
    assert "Dana" in p["speakers"]
    assert "You" in p["speakers"]
    assert not any(ln.strip().isdigit() for ln in p["lines"])  # seq numbers gone


def test_parse_txt_speaker_prefix():
    p = parse_transcript(TXT)
    assert p["speakers"] == ["Alice", "Bob"]
    assert len(p["lines"]) == 3


def test_parse_empty():
    assert parse_transcript("")["lines"] == []
    assert parse_transcript("WEBVTT\n\n")["lines"] == []


# ── connector (fetch) ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_transcript_single_summarized():
    path = _write(VTT, ".vtt")
    try:
        conn = TranscriptConnector()
        await conn.connect({"transcript_path": path, "meeting_title": "Q3 Roadmap"})
        items = await conn.fetch()
        assert len(items) == 1
        m = items[0]
        assert "Meeting transcript: Q3 Roadmap" in m["content"]
        assert "Participants: Alice, Bob" in m["content"]
        assert "Summary:" in m["content"]
        assert m["tags"] == ["meeting", "transcript"]
        assert m["metadata"]["speakers"] == ["Alice", "Bob"]
        assert m["metadata"]["summarized"] is True
        assert m["metadata"]["line_count"] == 3
        await conn.disconnect()
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_transcript_full_when_summarize_false():
    path = _write(TXT, ".txt")
    try:
        conn = TranscriptConnector()
        await conn.connect({"transcript_path": path, "summarize": False})
        items = await conn.fetch()
        assert "Transcript:" in items[0]["content"]
        assert "Postgres" in items[0]["content"]
        assert items[0]["metadata"]["summarized"] is False
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_transcript_dir():
    d = tempfile.mkdtemp()
    try:
        with open(os.path.join(d, "a.vtt"), "w") as f:
            f.write(VTT)
        with open(os.path.join(d, "b.srt"), "w") as f:
            f.write(SRT)
        with open(os.path.join(d, "ignore.pdf"), "w") as f:
            f.write("nope")
        conn = TranscriptConnector()
        await conn.connect({"transcript_dir": d})
        items = await conn.fetch()
        assert len(items) == 2
    finally:
        import shutil

        shutil.rmtree(d)


@pytest.mark.asyncio
async def test_transcript_connect_errors():
    conn = TranscriptConnector()
    with pytest.raises(FileNotFoundError):
        await conn.connect({"transcript_path": "/nope/x.vtt"})
    with pytest.raises(ValueError):
        await conn.connect({})


# ── registry ───────────────────────────────────────────────────────


def test_transcript_in_registry():
    names = {c["name"] for c in list_connectors()}
    assert "transcript" in names
    assert "vtt" in get_connector("transcript").help_text().lower()


# ── end-to-end via API ─────────────────────────────────────────────


@pytest_asyncio.fixture
async def api_client():
    from httpx import ASGITransport, AsyncClient

    import server.api as api_mod

    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    if api_mod._engine is not None:
        await api_mod._engine.shutdown()
    api_mod._engine = MemoryEngine(db_path=db_path, embedder_mode="hash", short_term_max=50)
    await api_mod._engine.initialize()
    api_mod._initialized = True
    transport = ASGITransport(app=api_mod.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await api_mod._engine.shutdown()
    api_mod._engine = None
    api_mod._initialized = False
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.mark.asyncio
async def test_api_transcript_import_and_ask(api_client):
    path = _write(VTT, ".vtt")
    try:
        r = await api_client.post(
            "/api/connectors/import",
            json={"connector": "transcript", "config": {"transcript_path": path, "meeting_title": "Q3 Roadmap"}, "project": "work"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["stored"] == 1

        r = await api_client.get("/api/memories", params={"source": "connector:transcript"})
        mems = r.json()
        assert len(mems) == 1
        assert mems[0]["project"] == "work"
        assert "meeting" in mems[0]["tags"]

        r = await api_client.post("/api/ask", json={"question": "who owns the mobile milestone?"})
        assert r.status_code == 200
        assert r.json()["sources"]
    finally:
        os.unlink(path)
