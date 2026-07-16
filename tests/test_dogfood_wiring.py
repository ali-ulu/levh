"""Dogfood runtime wiring (2.25.1).

The 2.25 audit found the journal existed but nothing attached it on the live
path. Locked here:
  - live wiring is OPT-IN: LEVH_DOGFOOD_ENABLED defaults to off and
    the shared engine provider attaches nothing without it;
  - when enabled, the provider-created engine journals automatically, to a
    file next to the SQLite database (or DOGFOOD_JOURNAL_PATH);
  - product surfaces emit: briefing, meeting prep, trust view, review
    actions, seed demo — not just store/recall;
  - attaching twice to the same engine never double-journals;
  - still no network, still no raw content.
"""

from __future__ import annotations

import os
import socket

import pytest
import pytest_asyncio

from server.core import engine_provider
from server.core.dogfood import (
    DogfoodJournal,
    default_journal_path_for,
    dogfood_enabled,
    maybe_attach,
    resolve_journal_path,
)
from server.core.memory_engine import MemoryEngine


@pytest_asyncio.fixture
async def engine(tmp_path):
    eng = MemoryEngine(db_path=str(tmp_path / "wire.db"), embedder_mode="hash")
    await eng.initialize()
    try:
        yield eng
    finally:
        await eng.shutdown()


def test_disabled_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("LEVH_DOGFOOD_ENABLED", raising=False)
    assert dogfood_enabled() is False
    eng = MemoryEngine(db_path=str(tmp_path / "off.db"), embedder_mode="hash")
    assert maybe_attach(eng) is None
    assert getattr(eng, "_dogfood_attached", False) is False


def test_provider_engine_attaches_only_when_enabled(monkeypatch, tmp_path):
    db = tmp_path / "prov.db"
    monkeypatch.setenv("SQLITE_DB_PATH", str(db))
    monkeypatch.setenv("EMBEDDER_MODE", "hash")

    # Off (default): provider engine has no journal listener.
    monkeypatch.delenv("LEVH_DOGFOOD_ENABLED", raising=False)
    engine_provider.set_engine(None)
    try:
        eng = engine_provider.get_engine()
        assert getattr(eng, "_dogfood_attached", False) is False

        # On: provider engine is instrumented.
        monkeypatch.setenv("LEVH_DOGFOOD_ENABLED", "true")
        engine_provider.set_engine(None)
        eng = engine_provider.get_engine()
        assert eng._dogfood_attached is True
    finally:
        engine_provider.set_engine(None)


def test_default_journal_path_sits_next_to_db(monkeypatch, tmp_path):
    monkeypatch.delenv("DOGFOOD_JOURNAL_PATH", raising=False)
    db = tmp_path / "data" / "mem.db"
    assert default_journal_path_for(str(db)) == str(
        (tmp_path / "data" / "dogfood_events.jsonl").resolve()
    )
    monkeypatch.setenv("DOGFOOD_JOURNAL_PATH", str(tmp_path / "elsewhere.jsonl"))
    assert default_journal_path_for(str(db)) == str(tmp_path / "elsewhere.jsonl")


def test_resolver_precedence(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "db" / "memory.db"))
    monkeypatch.setenv("DOGFOOD_JOURNAL_PATH", str(tmp_path / "env.jsonl"))
    assert resolve_journal_path(tmp_path / "explicit.jsonl") == str(tmp_path / "explicit.jsonl")
    assert resolve_journal_path() == str(tmp_path / "env.jsonl")
    monkeypatch.delenv("DOGFOOD_JOURNAL_PATH")
    assert resolve_journal_path() == str((tmp_path / "db" / "dogfood_events.jsonl").resolve())
    monkeypatch.delenv("SQLITE_DB_PATH")
    assert resolve_journal_path() == "./dogfood_events.jsonl"


def test_provider_and_cli_resolve_same_db_sibling(monkeypatch, tmp_path):
    db = tmp_path / "state" / "memory.db"
    monkeypatch.setenv("SQLITE_DB_PATH", str(db))
    monkeypatch.delenv("DOGFOOD_JOURNAL_PATH", raising=False)
    provider_path = default_journal_path_for(str(db))
    cli_path = resolve_journal_path(db_path=os.getenv("SQLITE_DB_PATH"))
    assert provider_path == cli_path


def test_double_attach_is_a_noop(engine, tmp_path):
    journal = DogfoodJournal(tmp_path / "j.jsonl")
    assert journal.attach(engine) is True
    assert journal.attach(engine) is False
    assert DogfoodJournal(tmp_path / "j2.jsonl").attach(engine) is False


@pytest.mark.asyncio
async def test_double_attach_never_double_journals(engine, tmp_path):
    journal = DogfoodJournal(tmp_path / "j.jsonl")
    journal.attach(engine)
    journal.attach(engine)
    await engine.store(content="single event please", memory_type="episodic", source="cli")
    stored = [e for e in journal.events() if e["event"] == "memory_stored"]
    assert len(stored) == 1


@pytest.mark.asyncio
async def test_product_surfaces_emit_dogfood_events(engine, tmp_path, monkeypatch):
    def _boom(*args, **kwargs):  # pragma: no cover
        raise AssertionError("dogfood wiring attempted network access")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)

    journal = DogfoodJournal(tmp_path / "j.jsonl")
    journal.attach(engine)

    mem = await engine.store(
        content="quarterly planning meets on tuesday", memory_type="episodic", source="cli"
    )
    await engine.recall("quarterly planning", top_k=3, reinforce=False)
    await engine.briefing()
    await engine.meeting_prep()
    await engine.get_trust(mem.id)
    await engine.apply_review(mem.id, "keep")
    await engine.apply_review(mem.id, "reinforce")
    await engine.apply_review(mem.id, "weaken")

    events = [e["event"] for e in journal.events()]
    for expected in (
        "memory_stored",
        "memory_recalled",
        "briefing_opened",
        "meeting_prep_opened",
        "trust_viewed",
        "review_keep",
        "review_reinforce",
        "review_weaken",
    ):
        assert expected in events, f"missing dogfood event {expected}"

    # No raw memory content in the journal.
    raw = journal.path.read_text(encoding="utf-8").lower()
    assert "quarterly planning" not in raw


@pytest.mark.asyncio
async def test_seed_demo_emits_completion_event(tmp_path):
    eng = MemoryEngine(db_path=str(tmp_path / "seed.db"), embedder_mode="hash")
    await eng.initialize()
    try:
        journal = DogfoodJournal(tmp_path / "j.jsonl")
        journal.attach(eng)
        await eng.seed_demo()
        assert journal.status()["seed_demo_completed"] is True
    finally:
        await eng.shutdown()


def test_admission_reason_codes_drive_duplicate_rate():
    from server.core.admission import evaluate

    assert evaluate("hello world")["reason_codes"] == ["admitted"]
    assert evaluate("x")["reason_codes"] == ["too_short"]
    assert evaluate("hello world", max_similarity=0.99)["reason_codes"] == ["duplicate_exact"]
    assert evaluate("hello world", max_similarity=0.93)["reason_codes"] == ["duplicate_near"]
    assert evaluate("token = sk-abc12345678secret")["reason_codes"] == ["secrets_redacted"]
