"""Procedural memory (#339) — the promotion rule, as a pure function.

The rule is deliberately offline and model-free, so these tests exercise it
directly: build a ``Memory``, call ``promotion_evidence``, assert on the
verdict. The end-to-end path (counters built by real recall, a proposal landing
in the findings inbox) is covered by the ``skill_promotion`` golden fixture in
``tests/test_memory_evaluation.py``; what is pinned here is every clause of the
rule and the reason each one exists.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio

os.environ.setdefault("EMBEDDER_MODE", "hash")

from server.core import procedure
from server.core.memory_engine import MemoryEngine
from server.core.types import RULE_TAG, Memory


def _memory(**overrides) -> Memory:
    """A memory that *would* be promoted, so each test changes one thing."""
    fields = {
        "content": "Restart the ingest worker before touching the queue.",
        "importance": 0.7,
        "recall_count": procedure.MIN_RECALLS,
        "frequency": procedure.MIN_FREQUENCY,
        "tags": ["ops-runbook"],
    }
    fields.update(overrides)
    return Memory(**fields)


# ── The happy path ────────────────────────────────────────────────────


def test_a_reused_memory_is_promoted_with_its_evidence():
    evidence = procedure.promotion_evidence(_memory(recall_count=4, frequency=5))

    assert evidence is not None
    assert evidence["recall_count"] == 4
    assert evidence["frequency"] == 5
    assert evidence["importance"] == 0.7


def test_the_threshold_is_inclusive():
    """Exactly at the bar is over it — otherwise the constants would be off by
    one and the fixture would have to guess which side is the real floor."""
    assert procedure.promotion_evidence(_memory()) is not None


# ── The three exclusions ──────────────────────────────────────────────


def test_a_pinned_memory_is_not_proposed():
    """A pinned memory is already exempt from decay; promoting it again says
    nothing new."""
    assert procedure.promotion_evidence(_memory(pinned=True)) is None


def test_an_already_promoted_memory_is_not_proposed_again():
    """Re-proposing a decision a human already made is how an inbox stops
    being read."""
    memory = _memory(tags=[procedure.PROCEDURE_TAG])

    assert procedure.promotion_evidence(memory) is None


def test_a_memory_with_a_violation_is_not_a_success_story():
    """The only clause that can fail a memory the counters alone would pass."""
    assert procedure.promotion_evidence(_memory(), violations=1) is None


# ── The counters ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "field, value",
    [
        ("recall_count", procedure.MIN_RECALLS - 1),
        ("frequency", procedure.MIN_FREQUENCY - 1),
        ("importance", procedure.MIN_IMPORTANCE - 0.1),
    ],
)
def test_each_counter_below_its_bar_blocks_promotion(field, value):
    assert procedure.promotion_evidence(_memory(**{field: value})) is None


def test_recalls_alone_are_not_enough():
    """A memory recalled repeatedly in one session is a hot query, not a skill:
    high recall with the default frequency (1) is not reuse."""
    assert procedure.promotion_evidence(_memory(frequency=1)) is None


# ── Rules and proposals ───────────────────────────────────────────────


def test_a_guard_rule_is_recognised_and_never_promoted():
    """Rules come from mistakes, procedures from successes — the two paths must
    not merge. A rule is pinned, so the rule catches it either way; the named
    predicate is what keeps the *intent* readable."""
    rule = _memory(tags=[RULE_TAG], pinned=True)

    assert procedure.is_rule(rule)
    assert procedure.promotion_evidence(rule) is None


def test_a_proposal_names_the_memory_and_the_next_step():
    memory = _memory(id="abc123")
    row = procedure.build_proposal(memory, procedure.promotion_evidence(memory))

    assert "Restart the ingest worker" in row["title"]
    assert memory.id in row["detail"]
    assert procedure.PROCEDURE_TAG in row["detail"]
    assert row["category"] == procedure.PROPOSAL_CATEGORY
    assert row["severity"] == procedure.PROPOSAL_SEVERITY


def test_a_proposal_title_is_stable_across_scans():
    """The finding fingerprint is built from the title, so a title that moved
    with the counters would file the same candidate as a new row every scan."""
    once = _memory(recall_count=3, frequency=3)
    many = _memory(recall_count=9, frequency=40)

    assert (
        procedure.build_proposal(once, procedure.promotion_evidence(once))["title"]
        == procedure.build_proposal(many, procedure.promotion_evidence(many))["title"]
    )


def test_a_repeated_proposal_folds_into_one_finding():
    """Re-running the scan must not grow the inbox. ``record_finding`` upserts
    on the fingerprint, which is the title plus the *shape* of the detail with
    numbers scrubbed — so the evidence counts moving between runs must not mint
    a new row."""
    from server.core.findings import build_row

    once = _memory(id="abc123", recall_count=3, frequency=3)
    many = _memory(id="abc123", recall_count=9, frequency=40)

    assert (
        build_row(**procedure.build_proposal(once, procedure.promotion_evidence(once)))[
            "id"
        ]
        == build_row(
            **procedure.build_proposal(many, procedure.promotion_evidence(many))
        )["id"]
    )


# ── The end-to-end path ───────────────────────────────────────────────


@pytest_asyncio.fixture
async def engine(tmp_path):
    eng = MemoryEngine(
        db_path=str(tmp_path / "procedure.db"), embedder_mode="hash", short_term_max=50
    )
    await eng.initialize()
    yield eng
    await eng.shutdown()


@pytest.mark.asyncio
async def test_propose_procedures_reads_real_counters_and_files_a_finding(engine):
    """The integration path: reuse is *built* by recall, not declared, and the
    proposal lands in the findings inbox with its evidence."""
    result = await engine.admit_memory(
        content="Restart the ingest worker with systemctl restart ingest-worker.",
        importance=0.7,
        source="cli",
        tags=["ops-runbook"],
    )
    memory_id = result["memory"]["id"]

    for _ in range(procedure.MIN_RECALLS):
        await engine.recall(
            query="How do I restart the ingest worker?", top_k=1, reinforce=True
        )

    proposals = await procedure.propose_procedures(engine)

    assert [p["memory_id"] for p in proposals] == [memory_id]
    findings = await engine.db.list_findings()
    assert any(f["id"] == proposals[0]["finding_id"] for f in findings)


@pytest.mark.asyncio
async def test_propose_procedures_does_not_promote_anything(engine):
    """Read-only with respect to memories: it proposes, a human promotes."""
    result = await engine.admit_memory(
        content="Restart the ingest worker with systemctl restart ingest-worker.",
        importance=0.7,
        source="cli",
    )
    memory_id = result["memory"]["id"]
    for _ in range(procedure.MIN_RECALLS):
        await engine.recall(query="restart the ingest worker", top_k=1, reinforce=True)

    before = await engine.get_memory(memory_id)
    await procedure.propose_procedures(engine)
    after = await engine.get_memory(memory_id)

    assert after.pinned is False
    assert procedure.PROCEDURE_TAG not in (after.tags or [])
    assert after.memory_type == before.memory_type
