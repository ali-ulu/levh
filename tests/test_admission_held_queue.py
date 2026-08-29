"""The admission gate's ``review`` verdict must hold a candidate, not drop it.

``review`` means "redundant but not identical, so a person decides". Before
``held_memories`` existed the verdict had no store behind it: the caller was
told the memory was not stored, and the content was gone. These tests pin the
holding area, the human decision on both sides, and the fact that admitting a
held candidate reproduces the memory the caller originally asked for.
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest
import pytest_asyncio

from server.core.memory_engine import MemoryEngine

NORMAL = "Atlas production database uses PostgreSQL with daily backups"
SECRET = "password=hunter2 for the production database"


def _review_decision(content: str, redacted: str | None = None) -> dict:
    """What ``server.core.admission.evaluate`` returns for a near-duplicate.

    The gate itself is pure and already covered by tests/test_admission.py; what
    is under test here is what the engine does once that verdict comes back, so
    the verdict is supplied rather than coaxed out of an embedder.
    """
    return {
        "action": "review",
        "reasons": ["possible duplicate (similarity 0.93)"],
        "reason_codes": ["duplicate_near"],
        "redacted_content": redacted if redacted is not None else content,
        "redacted": redacted is not None,
        "secrets": ["password"] if redacted is not None else [],
        "max_similarity": 0.93,
    }


@pytest_asyncio.fixture
async def engine():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    eng = MemoryEngine(db_path=path, embedder_mode="hash", short_term_max=20)
    await eng.initialize()
    yield eng
    await eng.shutdown()
    if os.path.exists(path):
        os.unlink(path)


@pytest_asyncio.fixture
async def api_client():
    from httpx import ASGITransport, AsyncClient
    import server.api as api_mod

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    if api_mod._engine is not None:
        await api_mod._engine.shutdown()
    api_mod._engine = MemoryEngine(db_path=path, embedder_mode="hash", short_term_max=20)
    await api_mod._engine.initialize()
    api_mod._initialized = True
    transport = ASGITransport(app=api_mod.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, api_mod._engine
    await api_mod._engine.shutdown()
    api_mod._engine = None
    api_mod._initialized = False
    if os.path.exists(path):
        os.unlink(path)


def _force_review(eng, redacted: str | None = None):
    async def _stub(content, project=None, min_length=3, exclude_id=None):
        return _review_decision(content, redacted)

    eng.evaluate_admission = _stub


@pytest.mark.asyncio
async def test_a_review_verdict_holds_the_content_instead_of_dropping_it(engine):
    _force_review(engine)
    result = await engine.admit_memory(content=NORMAL, project="atlas")

    assert result["stored"] is False
    assert result["memory"] is None
    # The whole defect in one assertion: "not stored" used to also mean "gone".
    assert result["held_id"]

    held = await engine.db.list_held_memories()
    assert [h["content"] for h in held] == [NORMAL]
    assert held[0]["max_similarity"] == 0.93
    assert json.loads(held[0]["reasons_json"]) == ["possible duplicate (similarity 0.93)"]


@pytest.mark.asyncio
async def test_a_held_candidate_is_not_a_memory(engine):
    _force_review(engine)
    await engine.admit_memory(content=NORMAL, project="atlas")

    # Holding must not be a back door into recall: an unadmitted candidate that
    # answered searches would make the gate decorative.
    assert await engine.episodic.count() == 0
    assert (await engine.recall(NORMAL, top_k=5)).memories == []


@pytest.mark.asyncio
async def test_a_rejected_candidate_is_still_dropped(engine):
    async def _stub(content, project=None, min_length=3, exclude_id=None):
        return {
            "action": "reject",
            "reasons": ["near-exact duplicate (similarity 0.99)"],
            "reason_codes": ["duplicate_exact"],
            "redacted_content": content,
            "redacted": False,
            "secrets": [],
            "max_similarity": 0.99,
        }

    engine.evaluate_admission = _stub
    result = await engine.admit_memory(content=NORMAL)

    # reject and review are different refusals. reject is the gate deciding --
    # holding those too would fill the queue with things nobody needs to judge.
    assert result["stored"] is False
    assert result["held_id"] is None
    assert await engine.db.list_held_memories() == []


@pytest.mark.asyncio
async def test_admitting_a_held_candidate_reproduces_the_original_memory(engine):
    _force_review(engine)
    held_id = (
        await engine.admit_memory(
            content=NORMAL,
            importance=0.87,
            tags=["atlas", "db"],
            project="atlas",
            source="claude-code",
            pinned=True,
            memory_type="episodic",
            metadata={"note": "carried through"},
        )
    )["held_id"]

    result = await engine.admit_held_memory(held_id)
    assert result["ok"] is True

    memory = result["memory"]
    # Everything the caller asked for must survive the detour through the
    # queue. A reconstruction that quietly drops importance or tags would be a
    # different memory wearing the same content.
    assert memory["content"] == NORMAL
    assert memory["importance"] == 0.87
    assert sorted(memory["tags"]) == ["atlas", "db"]
    assert memory["project"] == "atlas"
    assert memory["source"] == "claude-code"
    assert memory["pinned"] is True
    assert memory["memory_type"] == "episodic"
    assert memory["metadata"]["note"] == "carried through"
    # The override is recorded, not hidden.
    assert memory["metadata"]["admission"]["forced"] is True

    row = await engine.db.get_held_memory(held_id)
    assert row["status"] == "admitted"
    assert row["admitted_memory_id"] == memory["id"]


@pytest.mark.asyncio
async def test_a_candidate_can_only_be_decided_once(engine):
    _force_review(engine)
    held_id = (await engine.admit_memory(content=NORMAL))["held_id"]

    assert (await engine.admit_held_memory(held_id))["ok"] is True
    second = await engine.admit_held_memory(held_id)

    # Two agents racing, or one impatient double-click, must not turn one
    # candidate into two memories.
    assert second["ok"] is False
    assert second["error"] == "already_decided"
    assert await engine.episodic.count() == 1


@pytest.mark.asyncio
async def test_discarding_records_the_decision_rather_than_erasing_it(engine):
    _force_review(engine)
    held_id = (await engine.admit_memory(content=NORMAL))["held_id"]

    assert (await engine.discard_held_memory(held_id))["ok"] is True

    assert await engine.db.list_held_memories(status="held") == []
    decided = await engine.db.list_held_memories(status="")
    assert [(d["id"], d["status"]) for d in decided] == [(held_id, "discarded")]
    assert decided[0]["decided_at"]
    assert await engine.episodic.count() == 0


@pytest.mark.asyncio
async def test_a_secret_is_redacted_before_the_candidate_is_held(engine):
    _force_review(engine, redacted="password=[REDACTED] for the production database")
    held_id = (await engine.admit_memory(content=SECRET))["held_id"]

    row = await engine.db.get_held_memory(held_id)
    # A queue a person reads later is no place to park a live credential.
    assert "hunter2" not in row["content"]
    assert "[REDACTED]" in row["content"]


@pytest.mark.asyncio
async def test_a_missing_or_decided_candidate_answers_clearly(engine):
    _force_review(engine)
    assert (await engine.admit_held_memory("nope"))["error"] == "not_found"
    assert (await engine.discard_held_memory("nope"))["error"] == "not_found"

    held_id = (await engine.admit_memory(content=NORMAL))["held_id"]
    await engine.discard_held_memory(held_id)
    assert (await engine.admit_held_memory(held_id))["error"] == "already_decided"


@pytest.mark.asyncio
async def test_a_gated_import_holds_review_items_instead_of_dropping_them(engine):
    _force_review(engine)
    outcome = await engine.import_memories_gated(
        [{"content": NORMAL, "importance": 0.7, "project": "atlas"}]
    )

    assert outcome["held"] == 1
    # The breakdown already said "held". Nothing was holding them.
    held = await engine.db.list_held_memories()
    assert [h["content"] for h in held] == [NORMAL]


@pytest.mark.asyncio
async def test_the_held_queue_is_readable_and_decidable_over_http(api_client):
    client, eng = api_client
    _force_review(eng)
    await eng.admit_memory(content=NORMAL, project="atlas")

    listing = (await client.get("/api/memories/held")).json()
    assert listing["waiting"] == 1
    assert listing["held"][0]["content"] == NORMAL
    held_id = listing["held"][0]["id"]

    admitted = await client.post(f"/api/memories/held/{held_id}/admit")
    assert admitted.status_code == 200, admitted.text
    assert admitted.json()["memory"]["content"] == NORMAL

    assert (await client.get("/api/memories/held")).json()["waiting"] == 0
    assert (await client.post(f"/api/memories/held/{held_id}/admit")).status_code == 409
    assert (await client.post("/api/memories/held/nope/discard")).status_code == 404


@pytest.mark.asyncio
async def test_the_held_queue_is_not_the_spaced_repetition_review_queue(api_client):
    client, eng = api_client
    _force_review(eng)
    await eng.admit_memory(content=NORMAL)

    # Two different things named "review" live one path apart. /review is over
    # memories that were admitted and are fading; /held is over candidates that
    # were never admitted at all.
    assert (await client.get("/api/memories/review")).json()["review"] == []
    assert len((await client.get("/api/memories/held")).json()["held"]) == 1
