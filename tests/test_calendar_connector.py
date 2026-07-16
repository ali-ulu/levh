"""Tests for the Calendar (ICS) connector — the first work-life capture source.
All offline: a sample .ics string, no network, no API keys."""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["EMBEDDER_MODE"] = "hash"

from server.connectors import get_connector, list_connectors
from server.connectors.calendar import CalendarConnector, parse_ics
from server.core.memory_engine import MemoryEngine

SAMPLE_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:evt-1@test
SUMMARY:Q3 Roadmap Review
DTSTART:20260115T140000Z
DTEND:20260115T150000Z
LOCATION:Zoom
ORGANIZER;CN=Alice Smith:mailto:alice@example.com
ATTENDEE;CN=Bob Jones:mailto:bob@example.com
ATTENDEE;CN=Carol Lee:mailto:carol@example.com
DESCRIPTION:Discuss the Q3 plan\\nand budget
END:VEVENT
BEGIN:VEVENT
UID:evt-2@test
SUMMARY:Folded Summary That Is Long
 er Than One Line
DTSTART:20260116T090000Z
END:VEVENT
BEGIN:VEVENT
UID:evt-3-no-summary@test
DTSTART:20260117T090000Z
END:VEVENT
END:VCALENDAR
"""


def _write_ics(text: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".ics")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return path


# ── parser (unit) ──────────────────────────────────────────────────


def test_parse_ics_basic_fields():
    events = parse_ics(SAMPLE_ICS)
    assert len(events) == 3
    e = events[0]
    assert e["summary"] == "Q3 Roadmap Review"
    assert e["location"] == "Zoom"
    assert e["organizer"] == "Alice Smith"
    assert e["attendees"] == ["Bob Jones", "Carol Lee"]
    assert e["start"] == datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc)
    assert e["end"] == datetime(2026, 1, 15, 15, 0, tzinfo=timezone.utc)
    assert "and budget" in e["description"]  # \n unescaped


def test_parse_ics_line_unfolding():
    events = parse_ics(SAMPLE_ICS)
    assert events[1]["summary"] == "Folded Summary That Is Longer Than One Line"


def test_parse_ics_handles_malformed_gracefully():
    assert parse_ics("not a calendar at all") == []
    assert parse_ics("") == []


def test_parse_dt_variants():
    from server.connectors.calendar import _parse_dt

    assert _parse_dt("20260115T140000Z", {}) == datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc)
    assert _parse_dt("20260115", {"VALUE": "DATE"}) == datetime(2026, 1, 15, tzinfo=timezone.utc)
    assert _parse_dt("20260115T140000", {}) == datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc)
    assert _parse_dt("garbage", {}) is None


# ── connector (fetch) ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_calendar_connect_and_fetch():
    path = _write_ics(SAMPLE_ICS)
    try:
        conn = CalendarConnector()
        await conn.connect({"ics_path": path, "calendar_name": "Work"})
        items = await conn.fetch()
        # evt-3 has no summary -> skipped; 2 valid events
        assert len(items) == 2
        first = items[0]
        assert "Q3 Roadmap Review" in first["content"]
        assert "Alice Smith (organizer)" in first["content"]
        assert "Bob Jones" in first["content"]
        assert "Zoom" in first["content"]
        assert first["tags"] == ["calendar", "meeting"]
        assert first["metadata"]["calendar"] == "Work"
        assert first["metadata"]["event_uid"] == "evt-1@test"
        assert first["metadata"]["captured_at"] == "2026-01-15T14:00:00+00:00"
        await conn.disconnect()
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_calendar_sorted_soonest_first():
    path = _write_ics(SAMPLE_ICS)
    try:
        conn = CalendarConnector()
        await conn.connect({"ics_path": path})
        items = await conn.fetch()
        starts = [i["metadata"]["start"] for i in items]
        assert starts == sorted(starts)
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_calendar_window_filter():
    # Build an event 100 days in the future; future_days=30 should exclude it.
    future = datetime.now(timezone.utc) + timedelta(days=100)
    ics = (
        "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:f@t\nSUMMARY:Far Future Event\n"
        f"DTSTART:{future.strftime('%Y%m%dT%H%M%SZ')}\nEND:VEVENT\nEND:VCALENDAR\n"
    )
    path = _write_ics(ics)
    try:
        conn = CalendarConnector()
        await conn.connect({"ics_path": path, "future_days": 30})
        assert await conn.fetch() == []
        conn2 = CalendarConnector()
        await conn2.connect({"ics_path": path})  # no window -> included
        assert len(await conn2.fetch()) == 1
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_calendar_connect_errors():
    conn = CalendarConnector()
    with pytest.raises(FileNotFoundError):
        await conn.connect({"ics_path": "/nonexistent/nope.ics"})
    with pytest.raises(ValueError):
        await conn.connect({})  # neither ics_path nor ics_url


# ── registry ───────────────────────────────────────────────────────


def test_calendar_in_registry():
    names = {c["name"] for c in list_connectors()}
    assert "calendar" in names
    conn = get_connector("calendar")
    assert conn.name == "calendar"
    assert "ics" in conn.help_text().lower()


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
async def test_api_calendar_import(api_client):
    path = _write_ics(SAMPLE_ICS)
    try:
        r = await api_client.post(
            "/api/connectors/import",
            json={"connector": "calendar", "config": {"ics_path": path}, "project": "work"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["stored"] == 2

        # Stored as episodic memories with source connector:calendar
        r = await api_client.get("/api/memories", params={"source": "connector:calendar"})
        mems = r.json()
        assert len(mems) == 2
        assert all(m["project"] == "work" for m in mems)
        assert any("Q3 Roadmap Review" in m["content"] for m in mems)

        # And they answer questions
        r = await api_client.post("/api/ask", json={"question": "what meeting is about the roadmap?"})
        assert r.status_code == 200
        assert r.json()["sources"]
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_api_calendar_config_help(api_client):
    r = await api_client.get("/api/connectors/calendar/config")
    assert r.status_code == 200
    assert "ics" in r.json()["help"].lower()
