from __future__ import annotations

import pytest

from server.core.memory_engine import MemoryEngine


@pytest.mark.asyncio
async def test_empty_database_is_first_run_not_failure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    eng = MemoryEngine(db_path=str(tmp_path / "first.db"), embedder_mode="hash")
    await eng.initialize()
    try:
        status = await eng.onboarding_status()
        assert status["first_run"] is True
        assert status["database_initialized"] is True
        assert status["memory_count"] == 0
        assert status["ready"] is False
        assert status["recommended_next_step"] == "choose_demo_or_real_setup"
        assert next(c for c in status["checks"] if c["id"] == "memory")["status"] == "pending"
    finally:
        await eng.shutdown()


@pytest.mark.asyncio
async def test_real_memory_changes_recommendation_without_silent_boost(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    eng = MemoryEngine(db_path=str(tmp_path / "real.db"), embedder_mode="hash")
    await eng.initialize()
    try:
        mem = await eng.store(
            content="Atlas project uses PostgreSQL in production.",
            source="onboarding",
            project="getting-started",
            memory_type="episodic",
        )
        status = await eng.onboarding_status()
        assert status["first_run"] is False
        assert status["memory_count"] == 1
        assert status["recommended_next_step"] == "configure_mcp_client"
        assert mem.pinned is False
        assert mem.importance == 0.5
        assert set(mem.metadata) == {"embedding_provenance"}
        assert mem.metadata["embedding_provenance"]["provider"] == "hash"
    finally:
        await eng.shutdown()


@pytest.mark.asyncio
async def test_status_uses_live_profile_registry_and_safe_journal_summary(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "private" / "memory.db"
    monkeypatch.setenv("SQLITE_DB_PATH", str(db))
    eng = MemoryEngine(db_path=str(db), embedder_mode="hash")
    await eng.initialize()
    try:
        status = await eng.onboarding_status()
        assert status["profile_counts"] == {"minimal": 5, "work": 15, "admin": 54, "full": 59}
        assert status["mcp_default_profile"] == "work"
        assert status["profiles_are_security_boundary"] is False
        assert status["dogfood_enabled"] is False
        assert status["dogfood_journal"]["name"] == "dogfood_events.jsonl"
        assert str(tmp_path) not in str(status["dogfood_journal"])
    finally:
        await eng.shutdown()
