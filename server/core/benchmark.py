"""Recall-quality benchmark harness.

This module lives under ``server.core`` so it is included in built wheels.
The top-level ``scripts/benchmark_recall.py`` wrapper imports from here for
source-tree convenience; runtime API/CLI code must not depend on ``scripts``.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import tempfile

from server.core.memory_engine import MemoryEngine

DATASET: list[tuple[str, list[str]]] = [
    ("The production deploy branch is prod, not main", [
        "which branch do we deploy to production from",
        "The production deploy branch is prod, not main",
        # Paraphrase with no shared content word but the synonym layer: "ship"
        # and "release" expand to "deploy".
        "where do releases get shipped from",
    ]),
    ("API authentication uses JWT tokens with a 15 minute expiry", [
        "how does auth work and how long do tokens last",
        "API authentication uses JWT tokens with a 15 minute expiry",
        "how do users log in",
    ]),
    ("The Postgres connection pool max size is 20", [
        "what is the database connection pool limit",
        "The Postgres connection pool max size is 20",
        "how big is the sql connection pool",
    ]),
    ("Frontend state is managed with Zustand, not Redux", [
        "which state management library does the frontend use",
        "Frontend state is managed with Zustand, not Redux",
    ]),
    ("Rate limiting is 100 requests per minute per API key", [
        "what are the rate limits per key",
        "Rate limiting is 100 requests per minute per API key",
    ]),
    # Turkish is a first-class store language (#78). These queries arrive with
    # inflected, differently-worded forms of the stored terms, so they exercise
    # the language-agnostic stemming in server.core.lexical, not just translation.
    ("Veritabanı migrasyonu her gece saat ikide çalışır", [
        "migrasyonlar ne zaman çalışıyor",
        "Veritabanı migrasyonu her gece saat ikide çalışır",
        # Cross-language: English terms reach the Turkish memory through the
        # shipped dictionary (#78).
        "when do database migrations run",
    ]),
    ("Üretim ortamına deploy prod dalından yapılır", [
        "deploy hangi daldan yapılıyor",
        "Üretim ortamına deploy prod dalından yapılır",
        "which branch is used for a release",
    ]),
    ("Ödeme servisi hata durumunda üç kez yeniden dener", [
        "ödeme hatalarında kaç deneme yapılıyor",
        "Ödeme servisi hata durumunda üç kez yeniden dener",
    ]),
]

#: Unrelated memories plus deliberate near-misses: rows that share the surface
#: vocabulary of a query but answer a different question. Without them the
#: corpus is trivially separable and every floor sits at 1.0, so the gate cannot
#: observe a regression — a near-miss that starts winning is exactly the ranking
#: drop a real store sees.
DISTRACTORS = [
    "The office coffee machine is on the third floor",
    "Standup is at 10am every weekday",
    "The logo uses the hex color #7c3aed",
    "Vacation requests go through the HR portal",
    "The staging environment resets every night at 2am",
    "The staging deploy branch is stage, not prod",
    "The production database backup runs every night",
    "API authentication for the admin panel uses a password",
    "Ofis kahve makinesi üçüncü katta duruyor",
    "İzin talepleri insan kaynakları üzerinden yapılır",
    "Ödeme ekranı mobil uygulamada iki saniyede açılır",
]

#: Minimum metrics a mode must reach on :data:`DATASET`, checked in CI by
#: ``tests/test_benchmark_quality.py`` and by ``levh benchmark --check``.
#:
#: Only the model-free mode is gated. Hash + lexical ranking is deterministic —
#: no model, no network, no platform-specific arithmetic — so the same corpus
#: yields the same numbers everywhere, which is what makes a floor meaningful.
#: A semantic mode's numbers depend on the installed model and must be judged
#: on the machine that has it, not on a CI runner without one.
#:
#: These are the values the current corpus actually achieves. The corpus mixes
#: easy, exact queries with paraphrases that have no shared content word and
#: near-miss distractors, so the numbers sit below 1.0 and a weight tweak or a
#: candidate-source change moves them. A drop below these floors is a recall
#: regression and fails the build; raising the corpus difficulty is done by
#: editing the floors in the same commit, which is a deliberate and reviewable
#: act rather than a silent erasure of the signal.
QUALITY_FLOORS: dict[str, dict[str, float]] = {
    "hash": {"hit@1": 0.714, "hit@3": 0.905, "hit@5": 0.905, "mrr": 0.802},
}

#: Metrics that carry a quality signal, in report order.
GATED_METRICS = tuple(next(iter(QUALITY_FLOORS.values())).keys())


def quality_failures(metrics: dict) -> list[str]:
    """Metrics below their mode's floor, as ``"hit@1 0.875 < 1.0"`` lines.

    Empty when the mode is ungated (a semantic mode, or an unknown one) or when
    every gated metric clears its floor.
    """
    floors = QUALITY_FLOORS.get(metrics.get("embedder_mode", ""))
    if not floors:
        return []
    failures: list[str] = []
    for metric, floor in floors.items():
        value = metrics.get(metric)
        if value is None:
            failures.append(f"{metric} missing from the report")
        elif value < floor:
            failures.append(f"{metric} {value} < {floor}")
    return failures


async def run_benchmark(embedder_mode: str = "hash", top_k: int = 5) -> dict:
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    engine = MemoryEngine(db_path=db_path, embedder_mode=embedder_mode)
    await engine.initialize()
    try:
        gold_ids: list[str] = []
        for content, _queries in DATASET:
            mem = await engine.store(content=content, memory_type="episodic")
            gold_ids.append(mem.id)
        for content in DISTRACTORS:
            await engine.store(content=content, memory_type="episodic")

        ranks: list[int | None] = []
        for (_content, queries), gold_id in zip(DATASET, gold_ids):
            for query in queries:
                result = await engine.recall(query=query, top_k=top_k, reinforce=False)
                ids = [m.id for m in result.memories]
                ranks.append(ids.index(gold_id) + 1 if gold_id in ids else None)

        total = len(ranks)
        hit1 = sum(1 for r in ranks if r == 1) / total
        hit3 = sum(1 for r in ranks if r is not None and r <= 3) / total
        hit5 = sum(1 for r in ranks if r is not None and r <= 5) / total
        mrr = sum((1.0 / r) for r in ranks if r is not None) / total
        return {
            "embedder_mode": engine.embedder.mode,
            "requested_embedder_mode": embedder_mode,
            "queries": total,
            "hit@1": round(hit1, 3),
            "hit@3": round(hit3, 3),
            "hit@5": round(hit5, 3),
            "mrr": round(mrr, 3),
        }
    finally:
        await engine.shutdown()
        if os.path.exists(db_path):
            os.unlink(db_path)


def print_metrics(metrics: dict) -> None:
    print("\nLEVH recall benchmark")
    print("=" * 40)
    for key, value in metrics.items():
        print(f"  {key:24} {value}")
    print("=" * 40)
    if metrics.get("embedder_mode") == "hash":
        print(
            "Note: hash embedder is non-semantic — run with "
            "EMBEDDER_MODE=local/openai for a real quality signal."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="LEVH recall benchmark")
    parser.add_argument("--embedder", default=os.getenv("EMBEDDER_MODE", "hash"))
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if a gated metric is below its quality floor",
    )
    args = parser.parse_args()
    metrics = asyncio.run(run_benchmark(args.embedder, args.top_k))
    print_metrics(metrics)
    if not args.check:
        return 0
    failures = quality_failures(metrics)
    if failures:
        print("Quality gate failed:")
        for failure in failures:
            print(f"  {failure}")
        return 1
    if metrics.get("embedder_mode") in QUALITY_FLOORS:
        print(f"Quality gate passed for {metrics['embedder_mode']}.")
    else:
        print(f"No quality floor defined for {metrics.get('embedder_mode')}; nothing to gate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
