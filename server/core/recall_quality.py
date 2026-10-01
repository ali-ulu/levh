"""Recall quality from the log — the number ``recall_log`` exists to produce.

``server/core/db/recall_log.py`` records every recall's ranked result ids and
says why: the benchmark in ``benchmark.py`` measures ranking on a corpus its
author wrote, and *"the number a user actually wants — of the memories I was
handed, how many were worth having"* needs the queries they really asked. This
module computes that number. Until now nothing did.

WHAT "WORTH HAVING" MEANS HERE. The log stores no relevance labels, so this
does not invent any. A returned memory is counted as worth having when it is
still worth having *now*:

- it still exists (a forgotten or redacted memory is not preserved in the log
  by accident, and it is not a good answer either), and
- the same question has not since stopped returning it. When a query is asked
  twice and a memory is handed back the first time but not the second, the
  store has changed its mind about that memory. That is the closest the log
  gets to a staleness signal, and it is deliberately conservative: the churned
  id is dropped from the numerator rather than counted as a hit.

Everything else is arithmetic over ids the store itself produced. No LLM, no
network, no labels.

WHAT THIS IS NOT. Precision computed this way is a *lower bound*, and the
report says so in its own ``limits`` field. A memory can be returned, still
exist, never churn, and still be a bad answer; a memory can churn because it
was re-ranked, not because it went stale. The number is a signal to look at,
not a verdict — the same stance ``conflict.py`` and the admission gate take.

DETERMINISM. Two runs over the same log produce byte-identical output. Nothing
time- or random-dependent is generated here; the window is the log's own
oldest/newest row, not the time the report ran. ``evaluation.py`` holds the
same contract for the same reason.
"""

from __future__ import annotations

from collections import defaultdict

#: Bumped when a field changes meaning. A report on disk is only readable if
#: the reader knows which shape it is.
REPORT_VERSION = "recall-quality-v1"

#: Ids per existence query. SQLite's default parameter limit is 999 and a log
#: can name thousands of ids, so the lookup is chunked rather than one IN (…).
ID_CHUNK = 400


def _rate(numerator: int, denominator: int) -> float:
    """A ratio, or 0.0 when there is nothing to divide by.

    A log with no recalls has no precision — reporting 0.0 would read as
    "everything failed" when the truth is "nothing was asked". Callers pair
    this with the raw counts so an empty log is visible as empty.
    """
    return round(numerator / denominator, 4) if denominator else 0.0


def _empty_stats() -> dict:
    return {
        "returned": 0,
        "resolved": 0,
        "churned": 0,
        "empty_recalls": 0,
        "precision_at_k": 0.0,
        "hit_rate": 0.0,
    }


def _churned_by_query(rows: list[dict]) -> dict[str, set[str]]:
    """Ids each question was once given but no longer gets.

    Compared against the *most recent* recall of that query: a memory the store
    handed back and then stopped handing back for the same question is the
    closest thing the log has to a staleness signal. Retroactive on purpose —
    the stale id is not a hit in the earlier row either, which is what makes the
    resulting precision a lower bound.
    """
    ever: dict[str, set[str]] = defaultdict(set)
    latest: dict[str, set[str]] = {}
    for row in rows:
        key = row.get("query_sha256") or ""
        ids = set(row.get("result_ids") or [])
        ever[key] |= ids
        latest[key] = ids
    return {key: ids - latest[key] for key, ids in ever.items()}


def _score(rows: list[dict], existing_ids: set[str]) -> dict:
    """Micro-averaged precision@k and hit rate over ``rows`` (oldest first)."""
    stats = _empty_stats()
    churned_by_query = _churned_by_query(rows)
    stats["churned"] = sum(len(ids) for ids in churned_by_query.values())

    recalls_with_a_hit = 0
    gold_total = 0
    top_k_total = 0
    for row in rows:
        result_ids = row.get("result_ids") or []
        churned = churned_by_query.get(row.get("query_sha256") or "", set())

        top_k = int(row.get("top_k") or 0)
        in_top_k = result_ids[:top_k] if top_k > 0 else result_ids
        gold = [mid for mid in in_top_k if mid in existing_ids and mid not in churned]

        stats["returned"] += len(result_ids)
        stats["resolved"] += sum(1 for mid in result_ids if mid in existing_ids)
        if not result_ids:
            stats["empty_recalls"] += 1
        if gold:
            recalls_with_a_hit += 1
        gold_total += len(gold)
        top_k_total += len(in_top_k)

    stats["precision_at_k"] = _rate(gold_total, top_k_total)
    stats["hit_rate"] = _rate(recalls_with_a_hit, len(rows))
    return stats


def build_recall_report(
    rows: list[dict],
    *,
    existing_ids: set[str],
    store_size: int,
) -> dict:
    """Turn recall-log rows into a report. Pure — no DB, no clock.

    ``rows`` is oldest-first so that "the same question later stopped returning
    this id" is a statement about time. ``existing_ids`` is the subset of the
    log's ids that still resolve in ``memories``.
    """
    stats = _score(rows, existing_ids)
    projects: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        projects[row.get("project") or "(none)"].append(row)

    return {
        "report_version": REPORT_VERSION,
        "recalls": len(rows),
        "distinct_queries": len({r.get("query_sha256") for r in rows}),
        "window": {
            "oldest": rows[0].get("logged_at") if rows else None,
            "newest": rows[-1].get("logged_at") if rows else None,
        },
        "store_size": store_size,
        "results": stats,
        "by_project": [
            {"project": name, "recalls": len(group), **_score(group, existing_ids)}
            for name, group in sorted(projects.items())
        ],
        "limits": [
            "Relevance is inferred, not labelled: a memory counts as worth "
            "having when it still exists and its question still returns it.",
            "A memory re-ranked away by a later recall is treated as churned "
            "and dropped from the numerator, so precision is a lower bound.",
            "Recall logging is off by default (LEVH_RECALL_LOG); an empty "
            "report usually means nothing was recorded, not that recall failed.",
        ],
    }


async def _existing_ids(engine, ids: list[str]) -> set[str]:
    """Which of ``ids`` still resolve to a memory, chunked to stay under
    SQLite's bound-parameter limit."""
    found: set[str] = set()
    for start in range(0, len(ids), ID_CHUNK):
        chunk = ids[start : start + ID_CHUNK]
        for row in await engine.db.memories.get_memories_by_ids(chunk):
            found.add(row["id"])
    return found


async def gather_recall_report(engine, *, limit: int = 1000) -> dict:
    """Read the log from a live engine and build the report.

    ``list_recall_log`` caps a page at 1000 rows; the report says which slice
    it saw so a truncated window cannot be mistaken for the whole log.
    """
    page = max(1, min(int(limit), 1000))
    rows = list(reversed(await engine.db.list_recall_log(limit=page)))
    ids = sorted({mid for row in rows for mid in (row.get("result_ids") or [])})
    report = build_recall_report(
        rows,
        existing_ids=await _existing_ids(engine, ids),
        store_size=await engine.db.count_memories(),
    )
    report["log_window_cap"] = page
    stats = await engine.db.recall_log_stats()
    report["log_total"] = stats["total"]
    report["truncated"] = stats["total"] > len(rows)
    return report
