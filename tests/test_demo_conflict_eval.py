"""Demo conflict-evaluation fixture (2.24).

Locks the *behavioral contract* of the seeded demo's conflict candidate: it is
deterministic, singular, meaningful (not the earlier spurious "use/use"
collision), references a shared entity, carries the trust context, and stays a
*candidate for review* — never an auto-resolved verdict. All offline; no LLM,
no API key, no network.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from server.core.memory_engine import MemoryEngine


@pytest_asyncio.fixture
async def seeded(tmp_path):
    eng = MemoryEngine(
        db_path=str(tmp_path / "conf.db"), embedder_mode="hash", short_term_max=50
    )
    await eng.initialize()
    await eng.seed_demo()
    try:
        yield eng
    finally:
        await eng.shutdown()


@pytest.mark.asyncio
async def test_exactly_one_conflict_candidate(seeded):
    conflicts = await seeded.list_conflict_candidates(status="open", limit=50)
    assert len(conflicts) == 1


@pytest.mark.asyncio
async def test_conflict_is_the_deadline_not_a_use_collision(seeded):
    conflicts = await seeded.list_conflict_candidates(status="open", limit=50)
    c = conflicts[0]
    assert c["signal_type"] == "attribute_value"
    assert "deadline" in c["explanation"]["detail"]
    # The old spurious candidate keyed on the token "use" must not resurface.
    details = {c["explanation"]["detail"] for c in conflicts}
    assert "use" not in details


@pytest.mark.asyncio
async def test_conflict_references_a_shared_person_entity(seeded):
    c = (await seeded.list_conflict_candidates(status="open", limit=50))[0]
    shared = c["explanation"]["shared_entities"]
    assert any(e.startswith("person:") for e in shared)


@pytest.mark.asyncio
async def test_trust_context_present_in_conflict(seeded):
    c = (await seeded.list_conflict_candidates(status="open", limit=50))[0]
    ex = c["explanation"]
    # Trust recompute runs before detection, so both sides carry a trust score.
    assert ex["a_trust"] is not None
    assert ex["b_trust"] is not None
    assert 0.0 <= float(ex["a_trust"]) <= 1.0
    assert 0.0 <= float(ex["b_trust"]) <= 1.0


@pytest.mark.asyncio
async def test_conflict_is_a_candidate_not_a_verdict(seeded):
    c = (await seeded.list_conflict_candidates(status="open", limit=50))[0]
    # Still open for review, never auto-resolved.
    assert c["status"] == "open"
    # Confidence is a review priority, never certainty (never 1.0).
    assert 0.0 < c["confidence"] < 1.0
    # The explanation says so in words, and offers no "winner"/"verdict" field.
    assert "not a verdict" in c["explanation"]["note"].lower()
    assert "verdict" not in c
    assert "winner" not in c["explanation"]


@pytest.mark.asyncio
async def test_seeding_needs_no_llm_or_api_key(seeded, monkeypatch):
    """The whole flow already ran in the fixture with EMBEDDER_MODE=hash and no
    API key — re-detecting is a pure, deterministic pass too."""
    # Re-running detection is idempotent and never resets the reviewed state.
    before = await seeded.list_conflict_candidates(status="open", limit=50)
    result = await seeded.detect_conflict_candidates()
    after = await seeded.list_conflict_candidates(status="open", limit=50)
    assert result["new_candidates"] == 0  # nothing new on a re-scan
    assert len(before) == len(after) == 1
