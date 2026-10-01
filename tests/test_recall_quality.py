"""Recall quality from the log: the number the log exists to produce.

The properties pinned here are the ones that make the number trustworthy, and
each is a decision that could quietly invert:

1. An empty log is empty, not zero. "Nothing was asked" and "everything failed"
   are different facts and a report that conflates them is worse than no report.
2. Order decides precision. The log stores ids in rank order precisely so that
   gold-at-1 and gold-at-10 are distinguishable; a test over an unordered set
   would pass on an implementation that sorted the ids first.
3. A memory that no longer exists is not a good answer, and a memory a later
   recall of the same query churned is treated as stale — so the number is a
   lower bound and never a flattering one.
4. Two runs agree byte for byte. The report is a measurement; a timestamp in it
   would make every comparison of two runs a diff of when they ran.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio

os.environ["EMBEDDER_MODE"] = "hash"

from server.core.recall_quality import REPORT_VERSION, build_recall_report, gather_recall_report
from server.core.memory_engine import MemoryEngine


def _row(ids, *, top_k=5, project=None, sha="q1", logged_at="2026-01-01T00:00:00+00:00"):
    return {
        "query_sha256": sha,
        "result_ids": list(ids),
        "top_k": top_k,
        "project": project,
        "logged_at": logged_at,
    }


def test_an_empty_log_is_empty_not_zero():
    report = build_recall_report([], existing_ids=set(), store_size=0)

    assert report["report_version"] == REPORT_VERSION
    assert report["recalls"] == 0
    assert report["window"] == {"oldest": None, "newest": None}
    assert report["results"]["precision_at_k"] == 0.0
    assert report["results"]["empty_recalls"] == 0
    assert report["by_project"] == []
    assert report["limits"]


def test_rank_order_decides_precision():
    """Ids past top_k are not in the answer the user saw."""
    ranked = _row(["a", "b", "c", "d"], top_k=2)
    report = build_recall_report([ranked], existing_ids={"a", "b", "c", "d"}, store_size=4)

    assert report["results"]["precision_at_k"] == 1.0
    assert report["results"]["returned"] == 4

    # The same four ids, with the two that exist sitting below top_k.
    tail = _row(["c", "d", "a", "b"], top_k=2)
    tail_report = build_recall_report([tail], existing_ids={"a", "b"}, store_size=4)
    assert tail_report["results"]["precision_at_k"] == 0.0


def test_a_memory_that_no_longer_exists_is_not_gold():
    report = build_recall_report(
        [_row(["a", "b"])], existing_ids={"a"}, store_size=1
    )

    assert report["results"]["returned"] == 2
    assert report["results"]["resolved"] == 1
    assert report["results"]["precision_at_k"] == 0.5


def test_a_memory_a_later_recall_churned_is_dropped_from_the_numerator():
    """The store changed its mind about ``b``; it is not a hit in the first row."""
    rows = [_row(["a", "b"], sha="same"), _row(["a"], sha="same")]
    report = build_recall_report(rows, existing_ids={"a", "b"}, store_size=2)

    assert report["results"]["churned"] == 1
    # gold: row 1 -> a only (b churned); row 2 -> a. 2 gold over 3 in-top-k.
    assert report["results"]["precision_at_k"] == round(2 / 3, 4)


def test_a_churn_only_counts_within_the_same_question():
    rows = [_row(["a", "b"], sha="q1"), _row(["a"], sha="q2")]
    report = build_recall_report(rows, existing_ids={"a", "b"}, store_size=2)

    assert report["results"]["churned"] == 0
    assert report["results"]["precision_at_k"] == 1.0


def test_an_empty_result_row_is_counted_as_an_empty_recall():
    report = build_recall_report(
        [_row([]), _row(["a"], sha="q2")], existing_ids={"a"}, store_size=1
    )

    assert report["results"]["empty_recalls"] == 1
    assert report["results"]["hit_rate"] == 0.5


def test_two_runs_are_byte_identical():
    rows = [_row(["a", "b"], sha="q1"), _row(["b"], sha="q1"), _row(["c"], sha="q2")]
    first = build_recall_report(rows, existing_ids={"a", "b", "c"}, store_size=3)
    second = build_recall_report(rows, existing_ids={"a", "b", "c"}, store_size=3)

    assert first == second
    assert "generated_at" not in first


def test_projects_are_broken_out_and_sorted():
    rows = [
        _row(["a"], sha="q1", project="beta"),
        _row(["b"], sha="q2", project="alpha"),
        _row(["c"], sha="q3", project=None),
    ]
    report = build_recall_report(rows, existing_ids={"a", "b", "c"}, store_size=3)

    assert [p["project"] for p in report["by_project"]] == ["(none)", "alpha", "beta"]
    assert sum(p["recalls"] for p in report["by_project"]) == 3


@pytest_asyncio.fixture
async def engine():
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    eng = MemoryEngine(db_path=path, embedder_mode="hash", short_term_max=20)
    await eng.initialize()
    try:
        yield eng
    finally:
        await eng.shutdown()


@pytest.mark.asyncio
async def test_gathering_over_a_real_log_resolves_ids_against_the_store(engine, monkeypatch):
    """The ids in the log must be checked against live rows, not assumed live."""
    monkeypatch.setenv("LEVH_RECALL_LOG", "1")
    await engine.store(content="The production deploy branch is prod, not main")
    await engine.recall("which branch do we deploy to production from", top_k=3)

    report = await gather_recall_report(engine)

    assert report["recalls"] == 1
    assert report["store_size"] == 1
    assert report["results"]["returned"] >= 1
    assert report["results"]["resolved"] == report["results"]["returned"]
    assert report["truncated"] is False
    assert report["log_total"] == 1


@pytest.mark.asyncio
async def test_an_empty_store_reports_an_empty_log(engine):
    report = await gather_recall_report(engine)

    assert report["recalls"] == 0
    assert report["log_total"] == 0
    assert report["results"]["precision_at_k"] == 0.0
