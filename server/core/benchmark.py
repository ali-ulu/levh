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
    ]),
    ("API authentication uses JWT tokens with a 15 minute expiry", [
        "how does auth work and how long do tokens last",
        "API authentication uses JWT tokens with a 15 minute expiry",
    ]),
    ("The Postgres connection pool max size is 20", [
        "what is the database connection pool limit",
        "The Postgres connection pool max size is 20",
    ]),
    ("Frontend state is managed with Zustand, not Redux", [
        "which state management library does the frontend use",
        "Frontend state is managed with Zustand, not Redux",
    ]),
    ("Rate limiting is 100 requests per minute per API key", [
        "what are the rate limits per key",
        "Rate limiting is 100 requests per minute per API key",
    ]),
]

DISTRACTORS = [
    "The office coffee machine is on the third floor",
    "Standup is at 10am every weekday",
    "The logo uses the hex color #7c3aed",
    "Vacation requests go through the HR portal",
    "The staging environment resets every night at 2am",
]


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
    args = parser.parse_args()
    metrics = asyncio.run(run_benchmark(args.embedder, args.top_k))
    print_metrics(metrics)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
