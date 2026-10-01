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
- the same question, asked the same way, has not since stopped returning it.
  When a query is asked twice and a memory is handed back the first time but
  not the second, the store has changed its mind about that memory. That is the
  closest the log gets to a staleness signal, and it is deliberately
  conservative: the churned id is dropped from the numerator rather than
  counted as a hit.

Everything else is arithmetic over ids the store itself produced. No LLM, no
network, no labels.

WHAT THIS IS NOT — AND WHY IT IS NOT A BOUND. The resulting score is an
*existence-and-retention proxy*, not precision against labelled relevance, and
it has no guaranteed relationship to one in either direction:

- It can read **too high**: a memory can be returned, still exist, never churn,
  and still be a bad answer. One irrelevant-but-live result scores 1.0.
- It can read **too low**: a memory can churn because it was re-ranked, not
  because it went stale.

So it is neither an upper nor a lower bound — it is a proxy, and the report
says so in its own ``limits`` field rather than letting a reader assume the
label precision the name suggests. The value is in the trend across runs on one
store, not in the absolute number, which is the same stance ``conflict.py`` and
the admission gate take on their own inferred signals.

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


def _churn_key(row: dict) -> tuple:
    """What has to be equal for two recalls to be answering the same question.

    ``query_sha256`` alone is not enough: ``project`` and ``top_k`` change what
    a recall returns, so the same text under a different project or window is a
    different question and its results must not contaminate this one's churn.

    ``session_id`` is deliberately *not* in the key. The store is shared across
    sessions, so "this question stopped returning this memory" is a store-level
    fact; scoping churn to a session would hide exactly the drift the log
    exists to surface. ``min_importance`` is not persisted in ``recall_log``,
    so it cannot be part of the key without a schema change — a deliberate
    deferral, noted in the report's limits.
    """
    return (
        row.get("query_sha256") or "",
        row.get("project"),
        int(row.get("top_k") or 0),
    )


def _churned_by_query(rows: list[dict]) -> dict[tuple, set[str]]:
    """Ids each question was once given but no longer gets.

    Compared against the *most recent* recall of that question: a memory the
    store handed back and then stopped handing back is the closest thing the
    log has to a staleness signal. Retroactive on purpose — the stale id is not
    a hit in the earlier row either.
    """
    ever: dict[tuple, set[str]] = defaultdict(set)
    latest: dict[tuple, set[str]] = {}
    for row in rows:
        key = _churn_key(row)
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
        churned = churned_by_query.get(_churn_key(row), set())

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
            "This is an existence-and-retention proxy, not precision against "
            "labelled relevance, and it is not a bound in either direction: an "
            "irrelevant memory that still exists and never churns scores as a "
            "hit, and a memory re-ranked away counts as churn.",
            "Relevance is inferred, not labelled: a memory counts as worth "
            "having when it still exists and its question still returns it.",
            "Churn is keyed on query text, project and top_k. min_importance is "
            "not stored in the recall log, so two recalls differing only in "
            "that filter share a key.",
            "Recall logging is off by default (LEVH_RECALL_LOG); an empty "
            "report usually means nothing was recorded, not that recall failed.",
            "Read the trend across runs on one store, not the absolute number.",
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
