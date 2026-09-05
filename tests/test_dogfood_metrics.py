"""Dogfood journal (2.25) — local-only usage metrics.

Critical invariants locked here:
  - collection makes NO network calls (socket use is fenced off during ops);
  - raw memory content cannot enter the journal (attr whitelist) and never
    reaches the exported aggregate report;
  - timestamps are injectable → aggregation is deterministic;
  - export is an explicit action and writes aggregates only;
  - the engine listener journals ids/counts, never content.
"""

from __future__ import annotations

import json
import socket

import pytest
import pytest_asyncio

from server.core.dogfood import ALLOWED_ATTRS, EVENT_TYPES, DogfoodJournal


@pytest.fixture()
def journal(tmp_path):
    return DogfoodJournal(tmp_path / "dogfood_events.jsonl")


@pytest.fixture()
def no_network(monkeypatch):
    """Any attempt to reach the network during the test explodes.

    The trap is on connecting, not on constructing a socket: replacing
    ``socket.socket`` itself also breaks the event loop's own self-pipe, which
    on Windows made these tests hang forever instead of passing. Journalling
    never dials out, so a blocked ``connect`` proves exactly as much.
    """

    def _boom(*args, **kwargs):  # pragma: no cover - triggered only on violation
        raise AssertionError("dogfood journal attempted network access")

    monkeypatch.setattr(socket.socket, "connect", _boom, raising=False)
    monkeypatch.setattr(socket.socket, "connect_ex", _boom, raising=False)
    monkeypatch.setattr(socket, "create_connection", _boom)


def test_collection_makes_no_network_calls(journal, no_network, tmp_path):
    journal.record("memory_stored", when="2026-07-01T10:00:00+00:00")
    journal.record("memory_recalled", when="2026-07-01T10:05:00+00:00", attrs={"count": 3})
    journal.status()
    journal.export(tmp_path / "report.json")


def test_unknown_event_type_rejected(journal):
    with pytest.raises(ValueError):
        journal.record("memory_content_uploaded")


def test_content_shaped_attributes_rejected(journal):
    for bad in ("content", "text", "query", "body"):
        assert bad not in ALLOWED_ATTRS
        with pytest.raises(ValueError):
            journal.record("memory_stored", attrs={bad: "the secret launch plan"})
    # Oversized strings can't smuggle content through an allowed key either.
    with pytest.raises(ValueError):
        journal.record("memory_stored", attrs={"label": "x" * 500})


def test_status_aggregates_and_time_to_first_metrics(journal):
    journal.record("memory_stored", when="2026-07-01T10:00:00+00:00")
    journal.record("memory_recalled", when="2026-07-01T10:00:30+00:00")
    journal.record("recall_helpful", when="2026-07-01T10:01:00+00:00")
    journal.record("recall_not_helpful", when="2026-07-01T10:02:00+00:00")
    journal.record("briefing_opened", when="2026-07-01T10:03:00+00:00")
    journal.record("meeting_prep_opened", when="2026-07-01T10:04:00+00:00")
    journal.record("review_keep", when="2026-07-01T10:05:00+00:00")
    journal.record("review_forget", when="2026-07-01T10:06:00+00:00")

    s = journal.status()
    assert s["total_events"] == 8
    assert s["event_counts"]["memory_stored"] == 1
    assert s["time_to_first"]["time_to_first_recalled_seconds"] == 30.0
    assert s["time_to_first"]["time_to_first_briefing_seconds"] == 180.0
    assert s["time_to_first"]["time_to_first_meeting_prep_seconds"] == 240.0
    assert s["recall_feedback"]["helpful_rate"] == 0.5
    assert s["review_distribution"] == {"keep": 1, "forget": 1}
    # Deterministic: same journal, same aggregate.
    assert s == journal.status()


def test_export_is_aggregate_only_no_raw_lines(journal, tmp_path):
    journal.record(
        "memory_stored",
        when="2026-07-01T10:00:00+00:00",
        attrs={"memory_id": "mem-abc123"},
    )
    out = tmp_path / "report.json"
    journal.export(out)
    exported = out.read_text(encoding="utf-8")
    # The report carries counts, not per-event attributes such as memory ids.
    assert "mem-abc123" not in exported
    data = json.loads(exported)
    assert data["total_events"] == 1
    assert data["event_counts"] == {"memory_stored": 1}


def test_empty_journal_status_is_calm(journal):
    s = journal.status()
    assert s["total_events"] == 0
    assert s["seed_demo_completed"] is False
    assert s["recall_feedback"]["helpful_rate"] is None


def test_event_types_cover_required_dogfood_signals():
    required = {
        "memory_stored",
        "memory_recalled",
        "recall_helpful",
        "recall_not_helpful",
        "trust_viewed",
        "conflict_dismissed",
        "conflict_confirmed",
        "meeting_prep_opened",
        "briefing_opened",
        "review_keep",
        "review_forget",
    }
    assert required <= EVENT_TYPES


@pytest_asyncio.fixture
async def engine(tmp_path):
    from server.core.memory_engine import MemoryEngine

    eng = MemoryEngine(db_path=str(tmp_path / "dog.db"), embedder_mode="hash")
    await eng.initialize()
    try:
        yield eng
    finally:
        await eng.shutdown()


@pytest.mark.asyncio
async def test_engine_listener_journals_ids_not_content(journal, engine, no_network):
    journal.attach(engine)
    secret_text = "the tokyo acquisition closes on friday"
    await engine.store(content=secret_text, memory_type="episodic", source="cli")
    await engine.recall("acquisition closing date", top_k=3, reinforce=False)

    raw = journal.path.read_text(encoding="utf-8")
    assert "tokyo" not in raw.lower()
    events = [e["event"] for e in journal.events()]
    assert "memory_stored" in events
    assert "memory_recalled" in events
