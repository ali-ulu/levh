"""Recall quality from the log: the number the log exists to produce.

The properties pinned here are the ones that make the number trustworthy, and
each is a decision that could quietly invert:

1. An empty log is empty, not zero. "Nothing was asked" and "everything failed"
   are different facts and a report that conflates them is worse than no report.
2. Order decides precision. The log stores ids in rank order precisely so that
   gold-at-1 and gold-at-10 are distinguishable; a test over an unordered set
   would pass on an implementation that sorted the ids first.
3. A memory that no longer exists is not a hit, and a memory a later recall of
   the same question churned is treated as stale. This is a proxy for relevance,
   not labelled precision and not a bound in either direction — these tests pin
   the proxy's behaviour, not an accuracy claim.
4. Two runs agree byte for byte. The report is a measurement; a timestamp in it
   would make every comparison of two runs a diff of when they ran.
5. A churn is scoped to the question that produced it — same text, same project,
   same top_k. A different project asking the same text is a different question.
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


def test_the_same_text_under_a_different_project_is_a_different_question():
    """A narrower project returning fewer rows is not the store going stale."""
    rows = [
        _row(["a", "b"], sha="q1", project="wide"),
        _row(["a"], sha="q1", project="narrow"),
    ]
    report = build_recall_report(rows, existing_ids={"a", "b"}, store_size=2)

    assert report["results"]["churned"] == 0
    assert report["results"]["precision_at_k"] == 1.0


def test_the_same_text_under_a_different_top_k_is_a_different_question():
    rows = [_row(["a", "b"], sha="q1", top_k=2), _row(["a"], sha="q1", top_k=1)]
    report = build_recall_report(rows, existing_ids={"a", "b"}, store_size=2)

    assert report["results"]["churned"] == 0


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


# ── the command itself ───────────────────────────────────────────────
#
# The rendering is a thin layer, but it is where "empty is not zero" becomes
# user-visible and where --output/--json decide what an operator can diff
# between two runs. The command opens the store through ``engine_provider``,
# so these seed the pinned store from ``conftest`` and let the command find it
# the same way a user's CLI would.


def _seed_pinned_store(recall: bool, extra_recalls: int = 0) -> None:
    """Write into the store ``conftest`` pinned for this test, then let the
    command open it independently."""
    import asyncio

    path = os.environ["SQLITE_DB_PATH"]

    async def _run() -> None:
        eng = MemoryEngine(db_path=path, embedder_mode="hash")
        await eng.initialize()
        try:
            await eng.store(content="The production deploy branch is prod, not main")
            if recall:
                await eng.recall("which branch do we deploy to production from", top_k=2)
            for i in range(extra_recalls):
                await eng.recall(f"an extra question {i}", top_k=2)
        finally:
            await eng.shutdown()

    asyncio.run(_run())


def _recall_report_args(argv: list[str]):
    from server.cli_parsers import build_parser

    parser, _ = build_parser("levh")
    return parser.parse_args(argv)


def test_the_command_reports_an_empty_log_as_empty(monkeypatch, capsys):
    from server.commands.quality import cmd_recall_report

    _seed_pinned_store(recall=False)

    assert cmd_recall_report(_recall_report_args(["recall-report"])) == 0
    out = capsys.readouterr().out
    assert "No recalls logged" in out
    assert "precision@k" not in out


def test_the_command_renders_the_human_table(monkeypatch, capsys):
    from server.commands.quality import cmd_recall_report

    monkeypatch.setenv("LEVH_RECALL_LOG", "1")
    _seed_pinned_store(recall=True)

    assert cmd_recall_report(_recall_report_args(["recall-report"])) == 0
    out = capsys.readouterr().out
    assert "precision@k" in out
    assert "hit rate" in out
    assert "recalls" in out


def test_the_command_prints_json_and_writes_it(monkeypatch, capsys, tmp_path):
    """stdout must stay parseable JSON even when a file is also written."""
    import json as _json

    from server.commands.quality import cmd_recall_report

    monkeypatch.setenv("LEVH_RECALL_LOG", "1")
    _seed_pinned_store(recall=True)

    target = tmp_path / "recall.json"
    args = _recall_report_args(["recall-report", "--json", "-o", str(target)])
    assert cmd_recall_report(args) == 0

    captured = capsys.readouterr()
    # The status line goes to stderr, so stdout is exactly the document.
    assert "report →" in captured.err
    assert "report →" not in captured.out
    parsed = _json.loads(captured.out)
    assert parsed["report_version"] == "recall-quality-v1"
    assert "precision_at_k" in parsed["results"]

    written = target.read_text(encoding="utf-8")
    assert '"recall-quality-v1"' in written
    assert _json.loads(written)["results"]["precision_at_k"] == parsed["results"]["precision_at_k"]



def test_the_subcommand_is_wired_through_main(monkeypatch, capsys):
    """Typing ``levh recall-report`` must reach the command, not just exist in
    the parser — the dispatch chain is the part a user actually touches."""
    import sys

    import server.cli as cli

    monkeypatch.setenv("LEVH_RECALL_LOG", "1")
    _seed_pinned_store(recall=True)
    monkeypatch.setattr(sys, "argv", ["levh", "recall-report"])

    assert cli.main() == 0
    assert "recall quality" in capsys.readouterr().out


def test_a_truncated_window_is_flagged(monkeypatch, capsys):
    """A log longer than the window must say so; otherwise a slice reads as
    the whole store's quality."""
    from server.commands.quality import cmd_recall_report

    monkeypatch.setenv("LEVH_RECALL_LOG", "1")
    _seed_pinned_store(recall=True, extra_recalls=2)

    assert cmd_recall_report(_recall_report_args(["recall-report", "--limit", "1"])) == 0
    out = capsys.readouterr().out
    assert "NOTE:" in out
    assert "rows on disk" in out
