"""A re-broken attachment must show up as open again.

An attachment candidate's id is deterministic — ``attachment:<id>`` — and it
used to be written with ``insert_conflict_if_absent``. Once a candidate had
been resolved, a second break re-used that id, the insert was refused, and the
row stayed ``resolved``: a broken attachment with nothing open against it, and
``GET /api/conflicts?status=open`` reporting a clean bill of health.

The existing suite only walked ``changed -> restored -> resolved``. The
transition back the other way is what these tests pin.
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest
import pytest_asyncio

from server.core.memory_engine import MemoryEngine

NOTE = "Atlas incident review, with the console screenshot attached"
ORIGINAL = b"the bytes that were attached"
DIFFERENT = b"something else entirely"


@pytest_asyncio.fixture
async def engine(tmp_path):
    eng = MemoryEngine(
        db_path=str(tmp_path / "stackmemory.db"), embedder_mode="hash", short_term_max=20
    )
    await eng.initialize()
    yield eng
    await eng.shutdown()


@pytest_asyncio.fixture
async def attached(engine, tmp_path):
    memory = await engine.store(content=NOTE, memory_type="episodic")
    path = tmp_path / "screenshot.png"
    path.write_bytes(ORIGINAL)
    row = await engine.attach_file(memory.id, str(path))
    return engine, row["id"], path


async def _candidate(engine, attachment_id):
    return await engine.db.get_conflict(f"attachment:{attachment_id}")


@pytest.mark.asyncio
async def test_a_file_that_breaks_again_reopens_its_candidate(attached):
    engine, attachment_id, path = attached

    path.write_bytes(DIFFERENT)
    await engine.verify_attachment(attachment_id)
    assert (await _candidate(engine, attachment_id))["status"] == "open"

    path.write_bytes(ORIGINAL)
    await engine.verify_attachment(attachment_id)
    assert (await _candidate(engine, attachment_id))["status"] == "resolved"

    # The defect in one step: before this change the row stayed 'resolved'
    # while the file was broken again.
    path.write_bytes(DIFFERENT)
    await engine.verify_attachment(attachment_id)
    candidate = await _candidate(engine, attachment_id)
    assert candidate["status"] == "open"
    assert candidate["reviewed_at"] is None
    assert await engine.db.list_conflicts(status="open")


@pytest.mark.asyncio
async def test_a_file_that_goes_missing_again_reopens_its_candidate(attached):
    engine, attachment_id, path = attached

    path.unlink()
    await engine.verify_attachment(attachment_id)
    assert (await _candidate(engine, attachment_id))["status"] == "open"

    path.write_bytes(ORIGINAL)
    await engine.verify_attachment(attachment_id)
    assert (await _candidate(engine, attachment_id))["status"] == "resolved"

    path.unlink()
    await engine.verify_attachment(attachment_id)
    assert (await _candidate(engine, attachment_id))["status"] == "open"


@pytest.mark.asyncio
async def test_a_reopened_candidate_carries_the_current_signal(attached):
    engine, attachment_id, path = attached

    path.write_bytes(DIFFERENT)
    await engine.verify_attachment(attachment_id)
    first = await _candidate(engine, attachment_id)
    assert first["signal_type"] == "attachment_changed"

    path.write_bytes(ORIGINAL)
    await engine.verify_attachment(attachment_id)

    path.unlink()
    await engine.verify_attachment(attachment_id)
    reopened = await _candidate(engine, attachment_id)

    # A row that reopened still describing the previous break would send the
    # reviewer looking for the wrong thing.
    assert reopened["signal_type"] == "attachment_missing"
    explanation = json.loads(reopened["explanation_json"])
    assert explanation["signal_type"] == "attachment_missing"
    assert "no longer exists" in explanation["detail"]
    assert reopened["created_at"] >= first["created_at"]


@pytest.mark.asyncio
async def test_an_open_candidate_follows_the_file_from_changed_to_missing(attached):
    engine, attachment_id, path = attached

    path.write_bytes(DIFFERENT)
    await engine.verify_attachment(attachment_id)
    path.unlink()
    await engine.verify_attachment(attachment_id)

    # Never resolved in between, so nothing "reopens" — but the one open row
    # must describe the state the file is in now, not the one it was in.
    candidate = await _candidate(engine, attachment_id)
    assert candidate["status"] == "open"
    assert candidate["signal_type"] == "attachment_missing"


@pytest.mark.asyncio
async def test_a_dismissed_verdict_stands_while_the_signal_is_the_same(attached):
    engine, attachment_id, path = attached
    from datetime import datetime, timezone

    path.write_bytes(DIFFERENT)
    await engine.verify_attachment(attachment_id)
    await engine.db.update_conflict_status(
        f"attachment:{attachment_id}", "dismissed", datetime.now(timezone.utc).isoformat()
    )

    # Same break, looked at again. A person already decided about this exact
    # state; re-running verification is not new information.
    await engine.verify_attachment(attachment_id)
    candidate = await _candidate(engine, attachment_id)
    assert candidate["status"] == "dismissed"
    assert candidate["reviewed_at"] is not None


@pytest.mark.asyncio
async def test_a_dismissed_verdict_reopens_when_the_signal_is_new(attached):
    engine, attachment_id, path = attached
    from datetime import datetime, timezone

    path.write_bytes(DIFFERENT)
    await engine.verify_attachment(attachment_id)
    await engine.db.update_conflict_status(
        f"attachment:{attachment_id}", "dismissed", datetime.now(timezone.utc).isoformat()
    )

    # "I know it changed, that's fine" is not a decision about the file being
    # deleted. That is a fact they never saw.
    path.unlink()
    await engine.verify_attachment(attachment_id)
    candidate = await _candidate(engine, attachment_id)
    assert candidate["status"] == "open"
    assert candidate["signal_type"] == "attachment_missing"


@pytest.mark.asyncio
async def test_the_pairwise_contract_is_untouched(engine):
    """`insert_conflict_if_absent` must still refuse to reset a human verdict.

    That contract is right for pairwise candidates — a relationship between two
    memories does not change on its own — and this fix must not widen to them.
    """
    from datetime import datetime, timezone

    a = await engine.store(content="Atlas runs PostgreSQL", memory_type="episodic")
    b = await engine.store(content="Atlas runs MySQL", memory_type="episodic")
    row = {
        "id": f"{a.id}|{b.id}",
        "memory_id_a": a.id,
        "memory_id_b": b.id,
        "shared_entities_json": "[]",
        "signal_type": "contradiction",
        "confidence": 0.8,
        "status": "open",
        "explanation_json": "{}",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    assert await engine.db.insert_conflict_if_absent(row) is True
    await engine.db.update_conflict_status(
        row["id"], "dismissed", datetime.now(timezone.utc).isoformat()
    )

    assert await engine.db.insert_conflict_if_absent(row) is False
    assert (await engine.db.get_conflict(row["id"]))["status"] == "dismissed"


@pytest.mark.asyncio
async def test_verify_all_reports_the_re_broken_file(attached):
    engine, attachment_id, path = attached

    path.write_bytes(DIFFERENT)
    await engine.verify_all_attachments()
    path.write_bytes(ORIGINAL)
    await engine.verify_all_attachments()

    path.write_bytes(DIFFERENT)
    counts = await engine.verify_all_attachments()

    assert counts["changed"] == 1
    open_ids = [c["id"] for c in await engine.db.list_conflicts(status="open")]
    assert f"attachment:{attachment_id}" in open_ids
