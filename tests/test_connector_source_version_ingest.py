"""Connector source-version ingest contract.

The reconciliation path in ``_ingest_connector_source_version`` decides what
happens when the same external object is observed again: an identical snapshot
must be idempotent, a changed one must become a new version, and a stale one
must never overwrite a newer version. These tests pin down the edge cases the
happy path does not reach: rejected content, gate rejections, duplicated
current rows left behind by an interrupted run, and failed retirements.
"""

from __future__ import annotations

import os
import tempfile

import pytest
import pytest_asyncio

from server.core.memory_engine import MemoryEngine


REPO = "ali-ulu/levh"
SOURCE_ID = f"github:{REPO}:issue:42"


@pytest_asyncio.fixture
async def engine(monkeypatch):
    monkeypatch.setenv("EMBEDDER_MODE", "hash")
    monkeypatch.setenv("LEVH_SUPERSESSION", "0")
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    eng = MemoryEngine(db_path=db_path, embedder_mode="hash", short_term_max=10)
    await eng.initialize()
    yield eng
    await eng.shutdown()
    if os.path.exists(db_path):
        os.unlink(db_path)


def _item(
    content: str,
    *,
    identity: str = SOURCE_ID,
    revision: str = "2026-10-20T10:00:00Z",
    updated_at: str | None = "2026-10-20T10:00:00Z",
    state: str = "open",
) -> dict:
    metadata = {
        "source": "github",
        "type": "issue",
        "repo": REPO,
        "issue_number": 42,
        "state": state,
    }
    if identity:
        metadata["source_identity"] = identity
    if revision:
        metadata["source_revision"] = revision
    if updated_at is not None:
        metadata["updated_at"] = updated_at
    return {
        "content": content,
        "tags": ["github", "issue", f"repo:{REPO}"],
        "metadata": metadata,
    }


@pytest.mark.asyncio
async def test_item_without_source_identity_is_left_to_the_normal_path(engine):
    # No identity means the item is not a reconcilable source version; the
    # normal admission path handles it.
    result = await engine.ingest_items(
        [_item("A regular connector note without identity", identity="")],
        connector="github",
    )

    assert result["stored"] == 1


@pytest.mark.asyncio
async def test_item_without_source_identity_is_not_treated_as_a_source_version(engine):
    result = await engine.ingest_items(
        [_item("A regular connector note without identity", identity="")],
        connector="github",
        use_gate=False,
    )

    assert result["stored"] == 1
    current = await engine.list_memories(source="connector:github", limit=10)
    assert len(current) == 1
    assert "source_identity" not in (current[0].metadata or {})


@pytest.mark.asyncio
async def test_short_source_content_is_rejected(engine):
    result = await engine.ingest_items(
        [_item("ab", identity=SOURCE_ID)],
        connector="github",
    )

    assert result["stored"] == 0
    assert result["errors"] == 1
    current = await engine.list_memories(source="connector:github", limit=10)
    assert current == []


@pytest.mark.asyncio
async def test_non_comparable_updated_at_does_not_block_a_new_revision(engine):
    # An opaque (non-ISO) upstream timestamp is not comparable, so it must not
    # be treated as "older than" the current version.
    await engine.ingest_items(
        [_item("First version of the issue body", updated_at="not-a-timestamp")],
        connector="github",
    )
    result = await engine.ingest_items(
        [_item("Second version of the issue body", updated_at="also-not-a-timestamp")],
        connector="github",
    )

    assert result["stored"] == 1


@pytest.mark.asyncio
async def test_naive_updated_at_is_treated_as_utc(engine):
    # A timestamp without a timezone is read as UTC, so a naive older value
    # is correctly recognised as stale.
    await engine.ingest_items(
        [_item("Newer version of the issue body", updated_at="2026-10-20T12:00:00")],
        connector="github",
    )
    result = await engine.ingest_items(
        [_item("Older version of the issue body", updated_at="2026-10-20T11:00:00")],
        connector="github",
    )

    assert result["stored"] == 0
    assert result["duplicates"] == 1
    current = await engine.list_memories(source="connector:github", limit=10)
    assert len(current) == 1
    assert current[0].content == "Newer version of the issue body"


@pytest.mark.asyncio
async def test_missing_updated_at_is_not_treated_as_stale(engine):
    await engine.ingest_items(
        [_item("Newer version of the issue body", updated_at="2026-10-20T12:00:00Z")],
        connector="github",
    )
    result = await engine.ingest_items(
        [_item("Version without a comparable timestamp", updated_at=None)],
        connector="github",
    )

    assert result["stored"] == 1


@pytest.mark.asyncio
async def test_stale_revision_is_reported_as_a_duplicate_not_an_error(engine):
    await engine.ingest_items(
        [
            _item(
                "Newer version of the issue body",
                revision="2026-10-20T12:00:00Z",
                updated_at="2026-10-20T12:00:00Z",
            )
        ],
        connector="github",
    )
    result = await engine.ingest_items(
        [
            _item(
                "Older version of the issue body",
                revision="2026-10-20T11:00:00Z",
                updated_at="2026-10-20T11:00:00Z",
            )
        ],
        connector="github",
    )


@pytest.mark.asyncio
async def test_identical_snapshot_is_idempotent_and_creates_no_second_version(engine):
    item = _item("A stable source snapshot body")
    first = await engine.ingest_items([item], connector="github")
    second = await engine.ingest_items([item], connector="github")

    assert first["stored"] == 1
    assert second["stored"] == 0
    assert second["duplicates"] == 1
    assert second["errors"] == 0

    current = await engine.list_memories(source="connector:github", limit=10)
    assert len(current) == 1


@pytest.mark.asyncio
async def test_gate_rejection_of_a_source_snapshot_is_reported_as_an_error(engine):
    # The gate refuses the content, so nothing is stored and the failure is
    # surfaced instead of being swallowed.
    async def _reject(*args, **kwargs):
        return {
            "stored": False,
            "decision": {"action": "reject", "redacted": False},
            "memory": None,
            "held_id": None,
        }

    engine.admit_memory = _reject

    result = await engine.ingest_items(
        [_item("A snapshot the gate will refuse to admit")],
        connector="github",
    )

    assert result["stored"] == 0
    assert result["errors"] == 1


@pytest.mark.asyncio
async def test_failed_retirement_of_the_previous_version_is_reported(engine):
    # The new snapshot is stored, but retiring the previous one fails. The
    # snapshot stays stored and the failure is reported so a later run can
    # reconcile it.
    original = engine.db.retire_if_current

    async def _fail(memory_id, valid_to, superseded_by):
        if memory_id == first_id:
            raise RuntimeError("retirement failed")
        return await original(memory_id, valid_to, superseded_by)

    await engine.ingest_items(
        [_item("First version of the issue body")],
        connector="github",
    )
    current = await engine.list_memories(source="connector:github", limit=10)
    first_id = current[0].id

    engine.db.retire_if_current = _fail

    result = await engine.ingest_items(
        [_item("Second version of the issue body", revision="2026-10-20T11:00:00Z")],
        connector="github",
    )

    assert result["stored"] == 1
    assert result["errors"] == 1


@pytest.mark.asyncio
async def test_duplicate_current_rows_converge_on_the_newest_snapshot(engine):
    # An interrupted run can leave two current rows for the same snapshot.
    # Re-ingesting the identical snapshot must retire the older one instead of
    # creating a third row.
    await engine.ingest_items(
        [_item("A duplicated snapshot body")],
        connector="github",
    )
    current = await engine.list_memories(source="connector:github", limit=10)
    original_id = current[0].id
    snapshot_hash = current[0].metadata["source_snapshot_sha256"]

    stored = await engine.store(
        content="A duplicated snapshot body",
        importance=0.5,
        tags=["github", "issue", f"repo:{REPO}"],
        source="connector:github",
        memory_type="episodic",
        metadata={
            "source": "github",
            "type": "issue",
            "repo": REPO,
            "issue_number": 42,
            "state": "open",
            "source_identity": SOURCE_ID,
            "source_revision": "2026-10-20T10:00:00Z",
            "updated_at": "2026-10-20T10:00:00Z",
            "source_snapshot_sha256": snapshot_hash,
        },
    )
    assert stored.id != original_id

    result = await engine.ingest_items(
        [_item("A duplicated snapshot body")],
        connector="github",
    )

    assert result["stored"] == 0
    assert result["duplicates"] == 1

    current = await engine.list_memories(source="connector:github", limit=10)
    assert len(current) == 1
    assert current[0].id in {stored.id, original_id}

    # The row that was not chosen as canonical is retired, not left open.
    all_rows = await engine.list_memories(
        source="connector:github",
        include_superseded=True,
        limit=10,
    )
    assert len(all_rows) == 2
    retired = [row for row in all_rows if row.id != current[0].id]
    assert retired[0].valid_to is not None
    assert retired[0].superseded_by == current[0].id

    assert result["errors"] == 0
    assert result["duplicates"] == 1
    assert result["stored"] == 0


@pytest.mark.asyncio
async def test_source_version_without_gate_is_stored_directly(engine):
    result = await engine.ingest_items(
        [_item("A source snapshot stored without the admission gate")],
        connector="github",
        use_gate=False,
    )

    assert result["stored"] == 1
    current = await engine.list_memories(source="connector:github", limit=10)
    assert len(current) == 1
    assert current[0].metadata["source_revision"]
    assert current[0].metadata["source_snapshot_sha256"]
