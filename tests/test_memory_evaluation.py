"""Memory evaluation harness (2.25) — golden fixtures, determinism, privacy.

Critical invariants locked here:
  - the evaluation run is deterministic (two runs → identical reports);
  - raw memory content never leaks into the aggregate report;
  - all shipped golden fixtures pass on the real pipeline;
  - the known-false-positive fixture is *measured* (fp count ≥ 1), not hidden;
  - trust context stays advisory — the report is a measurement, not a verdict.
All offline: hash embedder, no LLM, no network.
"""

from __future__ import annotations

import json

import pytest

from server.core.evaluation import (
    DEFAULT_FIXTURE_DIR,
    EVALUATION_VERSION,
    load_fixtures,
    run_evaluation,
    seed_demo_completion,
)


@pytest.fixture(scope="module")
def report():
    import asyncio

    return asyncio.run(run_evaluation())


def test_fixture_set_covers_required_scenarios():
    names = {fx["name"] for fx in load_fixtures()}
    required = {
        "project_decision_superseded",
        "meeting_deadline_changed",
        "same_fact_independent_sources",
        "same_source_duplicate",
        "low_trust_imported_text",
        "confirmed_human_memory",
        "fading_important_memory",
        "redacted_secret",
        "conflict_false_positive",
    }
    assert required <= names


def test_all_golden_fixtures_pass(report):
    failed = [f["name"] for f in report["fixtures"] if not f["passed"]]
    assert failed == [], f"golden fixtures regressed: {failed}"


@pytest.mark.asyncio
async def test_evaluation_run_is_deterministic(report):
    second = await run_evaluation()
    assert json.dumps(report, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_report_is_versioned_and_labels_its_surfaces(report):
    assert report["evaluation_version"] == EVALUATION_VERSION
    assert report["levh_version"]
    # Bare "accuracy" is banned — each surface is named for what it measures.
    serialized = json.dumps(report)
    assert '"accuracy"' not in serialized
    for surface in ("recall", "quality", "conflicts", "lifecycle"):
        assert surface in report


def test_raw_memory_content_does_not_leak_into_report(report):
    serialized = json.dumps(report).lower()
    for fx in load_fixtures():
        for item in fx["memories"]:
            assert item["content"].lower() not in serialized
            # Even a distinctive fragment must not appear.
            fragment = item["content"].lower()[:40]
            assert fragment not in serialized
    # The redacted-secret fixture's secret must never appear anywhere.
    assert "sk-fixture12345secret" not in serialized


def test_recall_metrics_are_well_formed(report):
    r = report["recall"]
    assert r["queries"] > 0
    assert 0.0 <= r["hit_at_1"] <= r["hit_at_3"] <= 1.0
    assert 0.0 <= r["mrr"] <= 1.0
    # Superseded memories must not outrank their replacements.
    assert r["forbidden_violations"] == 0


def test_admission_quality_metrics(report):
    q = report["quality"]
    assert q["items"] > 0
    assert q["admission_mismatches"] == 0
    assert q["trust_label_mismatches"] == 0
    # The same-source duplicate fixture must register as a duplicate.
    assert q["duplicate_rate"] > 0
    # The redacted-secret fixture must register as a redaction.
    assert q["redaction_rate"] > 0
    # 2.25.1: duplicate_rate counts only duplicate reason codes, and the
    # low-trust metric names its scope (fixture-checked memories only).
    assert "checked_low_trust_count" in q
    assert "low_trust_count" not in q


def test_false_positive_conflict_is_measured_not_hidden(report):
    c = report["conflicts"]
    # The deliberate FP fixture keeps the false-positive surface visible.
    assert c["false_positives"] >= 1
    assert c["precision"] < 1.0
    # Real conflicts (superseded decision, changed deadline) are still found.
    assert c["recall"] == 1.0


def test_lifecycle_metrics(report):
    lc = report["lifecycle"]
    assert lc["review_distribution"].get("reinforce", 0) >= 1
    assert lc["review_distribution"].get("weaken", 0) >= 1
    # A reinforced fading memory must be recallable again.
    assert lc["fading_recovery_rate"] == 1.0


@pytest.mark.asyncio
async def test_seed_demo_completion_product_check():
    result = await seed_demo_completion()
    assert result["completed"] is True
    assert result["memories"] > 0
    assert result["conflict_candidates"] >= 1


@pytest.mark.asyncio
async def test_trust_does_not_alter_hscore_ranking():
    """Trust stays advisory: recall ranking must be identical whether or not
    trust scores have been computed."""
    import tempfile, os
    from server.core.memory_engine import MemoryEngine

    async def _ranked(with_trust: bool) -> list[int]:
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        eng = MemoryEngine(db_path=path, embedder_mode="hash")
        await eng.initialize()
        try:
            contents = [
                "The deploy branch for the atlas service is prod.",
                "Coffee machine maintenance happens on thursdays.",
                "Atlas service rollbacks are done with the revert script.",
            ]
            order = []
            for c in contents:
                m = await eng.store(content=c, memory_type="episodic", source="cli")
                order.append(m.id)
            if with_trust:
                await eng.recompute_trust_scores()
            res = await eng.recall("how do we deploy the atlas service", top_k=3, reinforce=False)
            return [order.index(m.id) for m in res.memories]
        finally:
            await eng.shutdown()
            os.unlink(path)

    assert await _ranked(False) == await _ranked(True)


def test_default_fixture_dir_is_the_shipped_golden_set():
    assert DEFAULT_FIXTURE_DIR.name == "evaluation"
    assert len(list(DEFAULT_FIXTURE_DIR.glob("*.json"))) >= 9
