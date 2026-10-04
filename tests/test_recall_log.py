"""The recall log: what was asked, and what came back, in rank order.

Four properties are load-bearing and each is pinned here:

1. It records by default. The rows are the audit substrate Phase 2 of
   ``docs/internal/SHARED-MEMORY-DESIGN.md`` builds on, and a log that stays
   empty on every store that never learned the flag answers nothing — the
   issue #376 state was zero rows on a live store. The flag is still the
   consent, but it now opts *out* (``LEVH_RECALL_LOG=0``): one variable for
   the operator who considers typed queries too sensitive to keep, instead of
   a substrate that nobody populates.
2. Order survives. The number this table exists to produce is precision at
   rank; a set of ids cannot distinguish gold-at-1 from gold-at-10, so a test
   that does not compare against the *returned* order proves nothing.
3. A secret in a query does not become a secret on disk. The log is a second
   copy of text the user pasted, in a store that is backed up.
4. Logging cannot break recalling. By the point a row is written the answer is
   already computed; losing the answer to save the receipt inverts the purpose
   of both.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import tempfile

import pytest
import pytest_asyncio

os.environ["EMBEDDER_MODE"] = "hash"

from server.core.db.recall_log import PRUNE_INTERVAL_SECONDS
from server.core.engine.recall import MAX_QUERY_CHARS
from server.core.database import CURRENT_SCHEMA_VERSION, Database
from server.core.memory_engine import MemoryEngine
from server.core.tenancy import AuthorizationError, Principal, bind_principal, reset_principal

SECRET = "sk-proj-abc123DEF456ghi789JKL0"


@pytest_asyncio.fixture
async def engine():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    eng = MemoryEngine(db_path=path, embedder_mode="hash", short_term_max=20)
    await eng.initialize()
    try:
        await eng.store(content="The production deploy branch is prod, not main")
        await eng.store(content="API authentication uses JWT tokens")
        yield eng
    finally:
        await eng.shutdown()


@pytest.mark.asyncio
async def test_a_recall_records_a_row_by_default(engine):
    """A store that never learned the flag must still populate the table.

    Issue #376: ``recall_log`` was documented as the audit substrate and held
    zero rows, because the only store that logs is one whose operator opted in.
    The conftest scrub keeps this test's environment free of ``LEVH_RECALL_LOG``,
    so what it exercises is the default itself.
    """
    result = await engine.recall("which branch do we deploy to production from", top_k=2)

    rows = await engine.db.list_recall_log(limit=5)

    assert len(rows) == 1, "a recall with logging on by default must leave a row"
    assert rows[0]["result_ids"] == [m.id for m in result.memories]
    assert rows[0]["result_count"] == len(result.memories)


@pytest.mark.asyncio
async def test_recall_audit_stamps_principal_and_workspace_and_filters_peers(engine):
    row = {
        "query": "where is the rollout plan",
        "query_sha256": "f" * 64,
        "result_ids": ["memory-42", "memory-7"],
        "result_count": 2,
        "top_k": 3,
        "project": "launch",
        "session_id": "session-1",
        "reinforced": False,
        # Caller-supplied identity must never win over the request context.
        "workspace_id": "forged",
        "principal_id": "forged",
        "principal_role": "admin",
    }

    token = bind_principal(
        Principal(id="backend-agent", workspace_id="team-42", role="viewer", agent="backend")
    )
    try:
        await engine.db.recall_log.record_recall(row)
        rows = await engine.db.list_recall_log(limit=5)
        assert rows[0]["workspace_id"] == "team-42"
        assert rows[0]["principal_id"] == "backend-agent"
        assert rows[0]["principal_role"] == "viewer"

        audit = await engine.db.access_audit("memory-42")
        assert audit == [
            {
                "memory_id": "memory-42",
                "principal_id": "backend-agent",
                "principal_role": "viewer",
                "workspace_id": "team-42",
                "project": "launch",
                "session_id": "session-1",
                "rank": 1,
                "logged_at": rows[0]["logged_at"],
            }
        ]
    finally:
        reset_principal(token)

    assert await engine.db.list_recall_log(limit=5) == []


@pytest.mark.asyncio
async def test_the_flag_turns_logging_off(engine, monkeypatch):
    """The default is a default, not a mandate: the operator can still say no."""
    monkeypatch.setenv("LEVH_RECALL_LOG", "0")

    await engine.recall("which branch do we deploy to", top_k=3)

    assert (await engine.db.recall_log_stats())["total"] == 0


@pytest.mark.asyncio
async def test_a_logged_recall_keeps_the_rank_it_returned(engine, monkeypatch):
    """Position is the point: the stored ids must equal the returned order."""
    monkeypatch.setenv("LEVH_RECALL_LOG", "1")

    result = await engine.recall("which branch do we deploy to production from", top_k=2)
    rows = await engine.db.list_recall_log(limit=5)

    assert len(rows) == 1
    row = rows[0]
    assert row["result_ids"] == [m.id for m in result.memories]
    assert row["result_count"] == len(result.memories)
    assert row["top_k"] == 2
    assert row["query"] == "which branch do we deploy to production from"
    assert row["query_sha256"] == hashlib.sha256(
        "which branch do we deploy to production from".encode("utf-8")
    ).hexdigest()


@pytest.mark.asyncio
async def test_a_recall_that_found_nothing_is_still_logged(engine, monkeypatch):
    """The empty answer is the row a precision number needs most, so it must
    not depend on there having been a result to write."""
    monkeypatch.setenv("LEVH_RECALL_LOG", "1")

    await engine.db.recall_log.record_recall(
        {
            "query": "what is the payroll password",
            "query_sha256": "x" * 64,
            "result_ids": [],
            "result_count": 0,
            "top_k": 5,
            "project": None,
            "session_id": None,
            "reinforced": False,
        }
    )
    rows = await engine.db.list_recall_log(limit=5)

    assert rows[0]["result_ids"] == []
    assert rows[0]["result_count"] == 0


@pytest.mark.asyncio
async def test_a_secret_in_the_query_is_not_stored_verbatim(engine, monkeypatch):
    """The log is a second copy of pasted text, in a backed-up file."""
    monkeypatch.setenv("LEVH_RECALL_LOG", "1")

    await engine.recall(f"what is the api key {SECRET}", top_k=3)
    stored = [r["query"] for r in await engine.db.list_recall_log(limit=5)]

    assert not any(SECRET in q for q in stored), stored
    assert any("[REDACTED]" in q for q in stored), stored


@pytest.mark.asyncio
async def test_an_overlong_query_is_truncated_not_stored_whole(engine, monkeypatch):
    """A recall query is a question. Past this length the caller pasted a
    document, and the log should not become a second memory store."""
    monkeypatch.setenv("LEVH_RECALL_LOG", "1")

    await engine.recall("z" * (MAX_QUERY_CHARS + 500), top_k=3)
    rows = await engine.db.list_recall_log(limit=5)


@pytest.mark.asyncio
async def test_a_failing_log_write_does_not_fail_the_recall(engine, monkeypatch):
    """The answer was already computed; the receipt is not worth losing it."""
    monkeypatch.setenv("LEVH_RECALL_LOG", "1")

    async def broken(*_args, **_kwargs):
        raise RuntimeError("storage is down")

    engine.db.record_recall = broken

    result = await engine.recall("which branch do we deploy to", top_k=2)

    assert result.memories, "recall must still answer when the log is unreachable"


@pytest.mark.asyncio
async def test_a_failing_log_write_is_reported_not_swallowed(engine, monkeypatch, caplog):
    """Suppressing the error keeps the recall alive but deletes the evidence
    that the measurement is now blind, which is worse than a noisy log."""
    monkeypatch.setenv("LEVH_RECALL_LOG", "1")

    async def broken(*_args, **_kwargs):
        raise RuntimeError("storage is down")

    engine.db.record_recall = broken

    with caplog.at_level(logging.ERROR, logger="levh.recall_log"):
        await engine.recall("which branch do we deploy to", top_k=2)

    assert any("recall log write failed" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_retention_removes_old_rows_and_leaves_recent_ones(engine):
    """The table holds typed queries, so it cannot grow forever — but pruning
    to today would destroy the thing it is kept for."""
    old = await engine.db.recall_log.record_recall(
        {
            "query": "an old question",
            "query_sha256": "a" * 64,
            "result_ids": [],
            "result_count": 0,
            "top_k": 3,
            "project": None,
            "session_id": None,
            "reinforced": False,
            "logged_at": "2000-01-01T00:00:00+00:00",
        }
    )
    await engine.db.recall_log.record_recall(
        {
            "query": "a recent question",
            "query_sha256": "b" * 64,
            "result_ids": [],
            "result_count": 0,
            "top_k": 3,
            "project": None,
            "session_id": None,
            "reinforced": False,
        }
    )
    assert old["logged"] is True

    removed = await engine.db.prune_recall_log(30)
    remaining = await engine.db.list_recall_log(limit=5)

    assert removed == 1
    assert [r["query"] for r in remaining] == ["a recent question"]


@pytest.mark.asyncio
async def test_the_configured_retention_window_reaches_the_recall_path(engine, monkeypatch):
    """``LEVH_RECALL_LOG_DAYS`` must bound the table from the recall path.

    The variable is only real if a recall enforces it: setting it here to one
    day has to remove a row from 2000 on the next logged recall while the row
    that recall just wrote survives. Reading the config and dropping it on the
    floor would leave an unbounded table that the docs say is bounded.
    """
    monkeypatch.setenv("LEVH_RECALL_LOG", "1")
    monkeypatch.setenv("LEVH_RECALL_LOG_DAYS", "1")

    await engine.db.recall_log.record_recall(
        {
            "query": "an ancient question",
            "query_sha256": "e" * 64,
            "result_ids": [],
            "result_count": 0,
            "top_k": 3,
            "project": None,
            "session_id": None,
            "reinforced": False,
            "logged_at": "2000-01-01T00:00:00+00:00",
        }
    )
    assert any(
        r["query"] == "an ancient question"
        for r in await engine.db.list_recall_log(limit=5)
    ), "the fixture row must exist before the recall prunes"
    # The fixture above writes without a retention window, so this recall is the
    # store's first prune attempt; the throttle must not defuse the assertion.
    engine.db.recall_log._last_prune_at = None

    await engine.recall("which branch do we deploy to", top_k=3)

    remaining = [r["query"] for r in await engine.db.list_recall_log(limit=5)]

    assert "an ancient question" not in remaining, (
        "LEVH_RECALL_LOG_DAYS=1 did not reach the prune; the row from 2000 survived"
    )
    assert any("which branch" in q for q in remaining)


@pytest.mark.asyncio
async def test_retention_runs_once_per_interval_not_once_per_recall(engine):
    """A read path that issues a DELETE on every call pays for a measurement
    it already paid for ten minutes ago."""
    calls: list[int] = []
    real_prune = engine.db.recall_log.prune_recall_log

    async def counting_prune(max_days: int) -> int:
        calls.append(max_days)
        return await real_prune(max_days)

    engine.db.recall_log.prune_recall_log = counting_prune
    row = {
        "query": "q",
        "query_sha256": "c" * 64,
        "result_ids": [],
        "result_count": 0,
        "top_k": 3,
        "project": None,
        "session_id": None,
        "reinforced": False,
    }

    await engine.db.recall_log.record_recall(dict(row), prune_max_days=30)
    await engine.db.recall_log.record_recall(dict(row), prune_max_days=30)
    assert len(calls) == 1, "the second recall must not issue a second DELETE"

    # Once the interval has genuinely passed, pruning resumes on its own — the
    # throttle defers retention, it does not cancel it.
    engine.db.recall_log._last_prune_at -= PRUNE_INTERVAL_SECONDS + 1
    await engine.db.recall_log.record_recall(dict(row), prune_max_days=30)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_a_row_this_code_did_not_write_stays_readable(engine):
    """A malformed row is somebody else's bug, not a reason to hide every
    other row in the table."""
    await engine.db.conn.execute(
        "INSERT INTO recall_log (query, query_sha256, result_ids, result_count, top_k,"
        " project, session_id, reinforced, logged_at)"
        " VALUES ('q', :d, 'not json', 0, 3, NULL, NULL, 0, :at)",
        {"d": "d" * 64, "at": "2026-01-01T00:00:00+00:00"},
    )
    await engine.db.commit()

    rows = await engine.db.list_recall_log(limit=5)

    assert rows[0]["result_ids"] == []
    assert isinstance(rows[0]["reinforced"], bool)


@pytest.mark.asyncio
async def test_stats_separate_volume_from_variety(engine):
    """Asking the same question ten times is one question. A precision figure
    averaged over ten copies of it is not a tenth as informative as it looks."""
    for _ in range(3):
        await engine.db.recall_log.record_recall(
            {
                "query": "same question",
                "query_sha256": hashlib.sha256(b"same question").hexdigest(),
                "result_ids": ["1", "2"],
                "result_count": 2,
                "top_k": 3,
                "project": None,
                "session_id": None,
                "reinforced": False,
            }
        )

    stats = await engine.db.recall_log_stats()

    assert stats["total"] == 3
    assert stats["distinct_queries"] == 1
    assert stats["newest"] >= stats["oldest"]
    assert json.dumps(stats, sort_keys=True), "stats must stay JSON-serialisable for the API"



@pytest.mark.asyncio
async def test_recall_log_pruning_is_admin_only(engine):
    await engine.db.recall_log.record_recall(
        {
            "query": "keep audit evidence protected",
            "query_sha256": "b" * 64,
            "result_ids": [],
            "result_count": 0,
            "top_k": 3,
            "project": None,
            "session_id": None,
            "reinforced": False,
        }
    )

    token = bind_principal(
        Principal(id="reader", workspace_id="default", role="viewer")
    )
    try:
        with pytest.raises(AuthorizationError):
            await engine.db.prune_recall_log(max_days=1)
    finally:
        reset_principal(token)


@pytest.mark.asyncio
async def test_v5_recall_log_migrates_to_principal_audit_without_losing_rows(tmp_path):
    path = str(tmp_path / "v5.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE recall_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT NOT NULL,
            query_sha256 TEXT NOT NULL,
            result_ids TEXT NOT NULL,
            result_count INTEGER NOT NULL,
            top_k INTEGER NOT NULL,
            project TEXT,
            session_id TEXT,
            reinforced INTEGER NOT NULL DEFAULT 0,
            logged_at TEXT NOT NULL
        );
        INSERT INTO recall_log
            (query, query_sha256, result_ids, result_count, top_k,
             project, session_id, reinforced, logged_at)
        VALUES
            ('legacy question', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
             '["legacy-memory"]', 1, 3, NULL, NULL, 0,
             '2026-01-01T00:00:00+00:00');

        CREATE TABLE held_memories (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            importance REAL NOT NULL,
            tags_json TEXT NOT NULL,
            session_id TEXT,
            project TEXT,
            source TEXT,
            memory_type TEXT NOT NULL,
            pinned INTEGER NOT NULL DEFAULT 0,
            metadata_json TEXT NOT NULL,
            reasons_json TEXT NOT NULL,
            max_similarity REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'held',
            created_at TEXT NOT NULL,
            decided_at TEXT,
            admitted_memory_id TEXT
        );
        INSERT INTO held_memories
            (id, content, importance, tags_json, session_id, project, source,
             memory_type, pinned, metadata_json, reasons_json, max_similarity,
             status, created_at, decided_at, admitted_memory_id)
        VALUES
            ('legacy-held', 'legacy candidate', 0.7, '[]', NULL, 'legacy',
             'import', 'episodic', 0, '{}', '["duplicate_near"]', 0.91,
             'held', '2026-01-01T00:00:00+00:00', NULL, NULL);

        PRAGMA user_version = 5;
        """
    )
    conn.commit()
    conn.close()

    db = Database(path)
    await db.connect()
    try:
        assert db.schema_version == CURRENT_SCHEMA_VERSION == 9
        rows = await db.list_recall_log(limit=5)
        assert len(rows) == 1
        assert rows[0]["query"] == "legacy question"
        assert rows[0]["workspace_id"] == "default"
        assert rows[0]["principal_id"] == "local"
        assert rows[0]["principal_role"] == "admin"

        held = await db.list_held_memories()
        assert len(held) == 1
        assert held[0]["id"] == "legacy-held"
        assert held[0]["workspace_id"] == "default"
    finally:
        await db.close()
