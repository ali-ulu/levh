"""External memory-benchmark adapter — retrieval-only, offline (issue #340).

`server/core/evaluation.py` answers *"did this change regress ranking on our
fixtures"*. It cannot answer *"how good is LEVH at long-term memory, compared
to anything"*, because its fixtures are self-authored. This module closes that
gap for one public benchmark, **LoCoMo** ([snap-research](https://snap-research.github.io/locomo)),
with the honest subset the issue asks for first: retrieval-side metrics that
need **no LLM judge**.

What it measures
----------------
Each LoCoMo conversation is fed through LEVH's real pipeline — the admission
gate, the store, then `recall` — one memory per dialogue turn, tagged with its
`dia_id`. For every labelled question the adapter checks whether the turn(s)
the dataset marks as evidence are retrieved within top-k. That is
recall@k / MRR on labelled evidence, and it is exactly what LEVH's retrieval
can be judged on without a model in the loop.

The **adversarial** category (5) is reported as an *evidence-retrieval proxy*,
not as answer abstention: LoCoMo does not label a session as absent from the
conversation, and a boolean `recall` has no notion of declining. Answering
"does LEVH abstain on adversarial questions" needs an LLM judge and is out of
scope here — the issue says to add that only later, clearly labelled.

Contracts
---------
- **No LLM, no network, no mocks.** The caller supplies the dataset file; this
  module never downloads it. With the hash embedder the run is deterministic.
- **Privacy.** The report carries counts, rates and category ids only — never
  a question, an answer, or a turn's text. A test scans the serialized report
  for fixture content.
- **Not a leaderboard claim.** The artifact is a reproducible measurement with
  its protocol and deviations recorded beside it.

Dataset licensing: LoCoMo is a research release. This module does not vendor
it. Point `--data` at a checkout of
``https://github.com/snap-research/locomo`` (``data/locomo10.json``).
"""

from __future__ import annotations

import json
import os
import tempfile
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from server.core.memory_engine import MemoryEngine

#: Bumped when the adapter's protocol changes, so an old artifact is
#: recognisable as having been produced a different way.
ADAPTER_VERSION = "levh-locomo-retrieval-v1"
BENCHMARK_ID = "locomo10"

#: The LoCoMo adversarial category. Every other category is reported under its
#: raw id; only this one is called out because the issue names it.
ADVERSARIAL_CATEGORY = 5

#: Default top-k. LoCoMo questions ask about one or two specific turns in a
#: ~400-turn conversation; 10 is the retrieval depth the golden evaluator also
#: uses (`_run_fixture` recalls at top_k=10).
DEFAULT_TOP_K = 10

#: Categories whose questions carry an evidence label worth scoring. LoCoMo
#: category 5 has `adversarial_answer` and an evidence turn but the question is
#: adversarial by construction, so it is scored separately.
SCORED_CATEGORIES = (1, 2, 3, 4)


def _package_version() -> str:
    try:
        return version("levh")
    except PackageNotFoundError:
        return "unknown"


def load_locomo(path: str | os.PathLike) -> list[dict]:
    """Load the LoCoMo release. Raises a clear error if the file is not it."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(
            f"LoCoMo dataset not found: {source}. Download "
            "https://github.com/snap-research/locomo -> data/locomo10.json and "
            "pass it with --data."
        )
    with open(source, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list) or not data or "conversation" not in data[0]:
        raise ValueError(
            f"{source} is not the LoCoMo release: expected a list of "
            "conversations with a 'conversation' key"
        )
    return data


def turns_of(conversation: dict) -> list[dict]:
    """Flatten a LoCoMo conversation into ordered turns with their dia_id.

    Session keys are ordered by their numeric suffix, not lexically, so
    session_2 does not sort after session_10. The date-time companion key is
    carried so a later temporal scenario can use it.
    """
    sessions = [
        key
        for key in conversation
        if key.startswith("session_") and not key.endswith("_date_time")
    ]
    sessions.sort(key=lambda k: int(k.rsplit("_", 1)[1]))
    turns: list[dict] = []
    for key in sessions:
        date = conversation.get(f"{key}_date_time", "")
        for turn in conversation.get(key, []):
            if not isinstance(turn, dict) or "text" not in turn:
                continue
            turns.append(
                {
                    "dia_id": turn.get("dia_id"),
                    "speaker": turn.get("speaker", ""),
                    "text": turn["text"],
                    "session": key,
                    "date": date,
                }
            )
    return turns


def _rate(n: int, d: int) -> float:
    return round(n / d, 4) if d else 0.0


async def _run_sample(sample: dict, embedder_mode: str, top_k: int) -> dict:
    """Feed one conversation through admission → store → recall.

    Returns per-sample counts and ranks only — no content.
    """
    turns = turns_of(sample.get("conversation", {}))
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = MemoryEngine(db_path=db_path, embedder_mode=embedder_mode)
    await engine.initialize()
    try:
        id_to_dia: dict[str, str] = {}
        gate_actions: dict[str, int] = {}
        for turn in turns:
            # Turns are pinned so the run is deterministic. Unpinned, recall
            # scores each memory through its wall-clock decay, and two runs a
            # few milliseconds apart flip near-ties — the report would differ
            # from itself. Pinning sets decay to 1.0 and isolates retrieval
            # from the decay simulation, which is what this benchmark measures.
            result = await engine.admit_memory(
                content=f"{turn['speaker']}: {turn['text']}",
                memory_type="episodic",
                source="locomo",
                pinned=True,
                metadata={"dia_id": turn["dia_id"], "session": turn["session"]},
            )
            action = result["decision"]["action"]
            gate_actions[action] = gate_actions.get(action, 0) + 1
            if result["stored"] and result["memory"]:
                id_to_dia[result["memory"]["id"]] = turn["dia_id"]

        queries: list[dict] = []
        for qa in sample.get("qa", []):
            evidence = qa.get("evidence") or []
            recalled = await engine.recall(
                query=qa["question"], top_k=top_k, reinforce=False
            )
            ranked = [id_to_dia.get(m.id) for m in recalled.memories]
            best_rank = None
            for dia_id in evidence:
                if dia_id in ranked:
                    rank = ranked.index(dia_id) + 1
                    best_rank = rank if best_rank is None else min(best_rank, rank)
            queries.append(
                {
                    "category": qa.get("category"),
                    "evidence_count": len(evidence),
                    "best_rank": best_rank,
                }
            )
        return {
            "sample_id": sample.get("sample_id", ""),
            "turns": len(turns),
            "stored": len(id_to_dia),
            "gate_actions": gate_actions,
            "queries": queries,
        }
    finally:
        await engine.shutdown()
        try:
            os.unlink(db_path)
        except OSError:
            pass


def _aggregate_ranks(ranks: list[int | None], top_k: int) -> dict:
    total = len(ranks)
    return {
        "questions": total,
        "hit_at_1": _rate(sum(1 for r in ranks if r == 1), total),
        "hit_at_3": _rate(sum(1 for r in ranks if r is not None and r <= 3), total),
        "hit_at_5": _rate(sum(1 for r in ranks if r is not None and r <= 5), total),
        f"hit_at_{top_k}": _rate(
            sum(1 for r in ranks if r is not None and r <= top_k), total
        ),
        "mrr": round(
            sum(1.0 / r for r in ranks if r is not None) / total, 4
        ) if total else 0.0,
    }


async def run_locomo(
    data_path: str | os.PathLike,
    embedder_mode: str = "hash",
    top_k: int = DEFAULT_TOP_K,
    limit: int | None = None,
) -> dict:
    """Run the retrieval-only LoCoMo evaluation and return the report.

    ``limit`` runs only the first N conversations, for a smoke run. The report
    records how many samples it actually covered, so a truncated run cannot be
    mistaken for the full release.
    """
    samples = load_locomo(data_path)
    samples_total = len(samples)
    if limit is not None:
        samples = samples[:limit]

    outcomes = [
        await _run_sample(sample, embedder_mode, top_k) for sample in samples
    ]

    scored = [
        q
        for o in outcomes
        for q in o["queries"]
        if q["category"] in SCORED_CATEGORIES and q["evidence_count"] > 0
    ]
    scored_ranks = [q["best_rank"] for q in scored]

    by_category: dict[str, dict] = {}
    for category in SCORED_CATEGORIES:
        cat_ranks = [
            q["best_rank"] for q in scored if q["category"] == category
        ]
        if cat_ranks:
            by_category[str(category)] = _aggregate_ranks(cat_ranks, top_k)

    adversarial = [
        q
        for o in outcomes
        for q in o["queries"]
        if q["category"] == ADVERSARIAL_CATEGORY and q["evidence_count"] > 0
    ]
    adv_ranks = [q["best_rank"] for q in adversarial]

    gate_actions: dict[str, int] = {}
    for o in outcomes:
        for action, count in o["gate_actions"].items():
            gate_actions[action] = gate_actions.get(action, 0) + count

    return {
        "benchmark": BENCHMARK_ID,
        "adapter_version": ADAPTER_VERSION,
        "levh_version": _package_version(),
        "embedder_mode": embedder_mode,
        "top_k": top_k,
        "samples": len(outcomes),
        "samples_total": samples_total,
        "turns": sum(o["turns"] for o in outcomes),
        "stored": sum(o["stored"] for o in outcomes),
        "admission": {
            "actions": gate_actions,
            "accept_rate": _rate(
                gate_actions.get("admit", 0) + gate_actions.get("redact", 0),
                sum(gate_actions.values()),
            ),
        },
        # Retrieval-only: recall@k over the turns LoCoMo labels as evidence.
        # No LLM judge, consistent with LEVH's no-model stance.
        "retrieval": _aggregate_ranks(scored_ranks, top_k),
        "retrieval_by_category": by_category,
        # Evidence-retrieval proxy for the adversarial slice. This is NOT an
        # abstention rate: recall cannot decline to answer, and LoCoMo does not
        # label a question as absent from the conversation. Answer abstention
        # needs an LLM judge and is deliberately out of scope.
        "adversarial": {
            "questions": len(adversarial),
            "evidence_retrieval_rate": _rate(
                sum(1 for r in adv_ranks if r is not None), len(adv_ranks)
            ),
            "metric_kind": "evidence_retrieval_proxy",
            "note": (
                "Not an abstention rate. LoCoMo does not label an adversarial "
                "question as absent from the conversation, and boolean recall "
                "cannot decline; answer abstention needs an LLM judge."
            ),
        },
        "protocol": {
            "unit": "one memory per dialogue turn, content 'speaker: text'",
            "ingest": "engine.admit_memory (real admission gate, per-sample store)",
            "recall": f"engine.recall(top_k={top_k}, reinforce=False)",
            "hit": "a question is a hit when any labelled evidence turn is retrieved",
            "judge": "none — retrieval-side only",
            "determinism": (
                "byte-identical reports with embedder_mode=hash: turns are "
                "pinned (decay=1.0) and the dataset is fixed"
            ),
            "deviations": [
                "Answers are not generated or scored; this measures retrieval.",
                "Adversarial questions are scored on evidence retrieval, not abstention.",
                "No session-date metadata is used for temporal questions (system-time only).",
                "Turns are pinned, so time decay does not affect the ranking.",
            ],
        },
    }


def render_report(report: dict) -> str:
    """Human-readable summary for the CLI. Never prints content."""
    lines = [
        "",
        "  LEVH external benchmark — LoCoMo (retrieval-only)",
        "  " + "=" * 48,
        f"  adapter          {report['adapter_version']}",
        f"  embedder_mode    {report['embedder_mode']}",
        f"  samples          {report['samples']} / {report['samples_total']}",
        f"  turns            {report['turns']} ({report['stored']} stored)",
        f"  admission accept {report['admission']['accept_rate']}",
        "  " + "-" * 48,
    ]
    r = report["retrieval"]
    lines += [
        f"  questions        {r['questions']}",
        f"  hit@1            {r['hit_at_1']}",
        f"  hit@3            {r['hit_at_3']}",
        f"  hit@5            {r['hit_at_5']}",
        f"  mrr              {r['mrr']}",
        "  " + "-" * 48,
    ]
    for category, stats in report["retrieval_by_category"].items():
        lines.append(
            f"  category {category:<6}    n={stats['questions']:<4} "
            f"hit@1={stats['hit_at_1']} mrr={stats['mrr']}"
        )
    adv = report["adversarial"]
    lines += [
        "  " + "-" * 48,
        f"  adversarial n={adv['questions']} "
        f"evidence_retrieval={adv['evidence_retrieval_rate']} (proxy, not abstention)",
        "  " + "=" * 48,
    ]
    if report["embedder_mode"] == "hash":
        lines.append(
            "  Note: the hash embedder is non-semantic, so this is a lexical "
            "floor, not a semantic result."
        )
    return "\n".join(lines)
