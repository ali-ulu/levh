"""The read-only MCP profile must leave the store unchanged — measured in SQL.

``read_only`` is an allowlist enforced at the dispatcher
(:mod:`server.tools.access`), but two write paths sit *below* the tool surface
where that guard cannot see them:

* the ``recall_log`` row (plus its retention DELETE) that every recall writes,
* the derived-state rebuild (entity / trust / conflict tables) that a
  freshness-required read such as ``memory_trust`` triggers.

Both check ``MemoryEngine.read_only``, which ``register_all_tools`` sets from
the resolved profile. These tests spy on the connection and assert the store is
never written, so the guarantee is measured where it matters — the database —
rather than on the advertised tool list.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
import pytest_asyncio
from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine
from server.tools.register import register_all_tools

WRITE_SQL = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE|REPLACE|DROP|ALTER|CREATE|TRUNCATE)\b", re.IGNORECASE
)


class WriteSpy:
    """Record every write statement issued through an ``aiosqlite`` connection."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn
        self._originals: dict[str, Any] = {}
        self.statements: list[str] = []

    def __enter__(self) -> "WriteSpy":
        for name in ("execute", "executemany", "executescript"):
            original = getattr(self._conn, name)
            self._originals[name] = original
            setattr(self._conn, name, self._wrap(original))
        return self

    def __exit__(self, *exc: object) -> bool:
        for name, original in self._originals.items():
            setattr(self._conn, name, original)
        return False

    def _wrap(self, original: Any) -> Any:
        async def wrapper(sql: Any, *args: Any, **kwargs: Any) -> Any:
            if isinstance(sql, str) and WRITE_SQL.match(sql):
                self.statements.append(" ".join(sql.split())[:120])
            return await original(sql, *args, **kwargs)

        return wrapper


@pytest_asyncio.fixture
async def engine(tmp_path):
    eng = MemoryEngine(
        db_path=str(tmp_path / "read-only-writes.db"),
        embedder_mode="hash",
        short_term_max=50,
    )
    await eng.initialize()
    try:
        yield eng
    finally:
        await eng.shutdown()


@pytest.mark.asyncio
async def test_read_only_profile_issues_no_write_statements(engine, monkeypatch):
    """Every read the profile advertises, run with the store dirty, writes nothing."""
    monkeypatch.setenv("LEVH_RECALL_LOG", "1")
    alice = await engine.store("Alice prefers SQLite for local storage", memory_type="episodic")
    await engine.store("Acme Corp uses Postgres", memory_type="episodic")
    await engine.recompute_derived_state()
    # A write after the rebuild leaves the derived views dirty, so the
    # freshness-required reads below take the rebuild path that used to write.
    await engine.store("Carol likes MySQL", memory_type="episodic")
    engine.read_only = True

    with WriteSpy(engine.db.conn) as spy:
        await engine.recall("what does Alice prefer?", top_k=3)  # reinforce defaults on
        await engine.ask("what does Alice prefer?")
        await engine.get_trust(alice.id)
        await engine.list_low_trust()
        await engine.list_conflict_candidates()
        await engine.list_entities_graph()
        await engine.get_entity("Alice")
        await engine.get_context(max_tokens=500, query="Alice")
        await engine.briefing(days=7)
        await engine.meeting_prep(query="Alice", within_days=30)
        await engine.get_related(alice.id)
        await engine.timeline(days=30)
        await engine.get_stats()

    assert spy.statements == []


@pytest.mark.asyncio
async def test_read_only_recall_writes_no_recall_log_row(engine, monkeypatch):
    monkeypatch.setenv("LEVH_RECALL_LOG", "1")
    await engine.store("Alice prefers SQLite", memory_type="episodic")
    engine.read_only = True

    await engine.recall("Alice", top_k=3)
    await engine.ask("Alice?")

    assert await engine.db.list_recall_log(limit=10) == []


@pytest.mark.asyncio
async def test_read_only_recall_does_not_reinforce_at_the_engine(engine):
    """Defence in depth: the engine refuses to reinforce even when asked."""
    memory = await engine.store("Alice prefers SQLite", memory_type="episodic")
    before = (await engine.get_memory(memory.id)).frequency
    engine.read_only = True

    await engine.recall("Alice", top_k=3)  # reinforce defaults to True

    assert (await engine.get_memory(memory.id)).frequency == before


@pytest.mark.asyncio
async def test_read_only_derived_reads_serve_stored_rows_without_rebuilding(engine):
    memory = await engine.store("Alice prefers SQLite", memory_type="episodic")
    await engine.recompute_derived_state()
    stored = await engine.get_trust(memory.id)
    await engine.store("Carol likes MySQL", memory_type="episodic")  # dirty again
    engine.read_only = True

    assert await engine.get_trust(memory.id) == stored


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        ("read_only", True),
        ("minimal", False),
        ("work", False),
        ("admin", False),
        ("full", False),
        (None, False),
    ],
)
async def test_register_all_tools_binds_the_engine_read_only_flag(engine, profile, expected):
    register_all_tools(FastMCP("profile-flag-test"), engine, profile=profile)
    assert engine.read_only is expected


@pytest.mark.asyncio
async def test_dispatched_read_tool_under_read_only_writes_nothing(engine, monkeypatch):
    """The whole path — dispatcher, tool, engine — leaves the store untouched."""
    monkeypatch.setenv("LEVH_RECALL_LOG", "1")
    await engine.store("Alice prefers SQLite", memory_type="episodic")
    mcp = FastMCP("read-only-writes-test")
    register_all_tools(mcp, engine, profile="read_only")

    with WriteSpy(engine.db.conn) as spy:
        await mcp.call_tool("recall_memory", {"query": "Alice"})
        await mcp.call_tool("get_memory_stats", {})

    assert spy.statements == []
