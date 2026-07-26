"""Offline weight tuning for the H(x,ψ) recall score.

The four H(x,ψ) weights (``HSCORE_ALPHA``/``BETA``/``GAMMA``/``DELTA``) ship as
hand-picked defaults. This module fits them to a labelled query set instead,
using a gradient-free search, and reports what the change is actually worth.

Nothing here runs at recall time. ``levh tune`` is an offline analysis command:
it prints recommended ``HSCORE_*`` values and leaves it to you to adopt them.
Default runtime behaviour is unchanged.

Why the search is cheap: similarity, decay, importance and frequency do not
depend on the weights at all — only the way they are *combined* does. So the
per-query candidate features are collected once from the real recall path, and
every candidate weight vector is then scored with pure arithmetic. The ranking
this reproduces is identical to ``MemoryEngine.recall``.

Honesty note, enforced in the report: the built-in labelled set is small. Fitting
four weights to it would overfit badly, so the reported gain is always
**leave-one-group-out cross-validated** — weights are fitted on some query groups
and scored on a group they never saw. An in-sample number would look better and
mean less.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np

from server.core.hscore import DEFAULT_WEIGHTS, HScoreCalculator, HScoreWeights
from server.core.memory_engine import MemoryEngine

WEIGHT_NAMES = ("alpha", "beta", "gamma", "delta")


@dataclass(frozen=True)
class LabelledMemory:
    """A corpus memory with the decay-state attributes H(x,ψ) actually reads."""

    content: str
    importance: float
    age_hours: float
    frequency: int
    stability_hours: float = 168.0


@dataclass(frozen=True)
class LabelledTopic:
    """One retrieval task: queries, the correct answer, and a stale decoy.

    The decoy deliberately shares vocabulary with the queries, so under a purely
    lexical embedder it can out-score the gold memory on similarity alone. Only
    the decay, importance and frequency terms can pull the current fact back to
    the top — which is exactly the behaviour these weights exist to produce.
    """

    queries: tuple[str, ...]
    gold: LabelledMemory
    decoy: LabelledMemory


# The superseded-fact corpus. `benchmark.DATASET` cannot tune weights: every
# memory there is stored fresh with identical importance (0.5), frequency (1)
# and zero elapsed time, so three of the four H(x,ψ) terms are the same constant
# for every candidate and cannot reorder anything. This corpus varies exactly
# those attributes, and encodes the failure mode the agent-memory literature
# flags as the hard one: a frequently-recalled memory that has become false.
TUNING_CORPUS: tuple[LabelledTopic, ...] = (
    LabelledTopic(
        queries=(
            "which branch do we deploy to production from",
            "what is the production deploy branch",
        ),
        gold=LabelledMemory(
            "The production deploy branch is prod",
            importance=0.9, age_hours=2.0, frequency=12, stability_hours=900.0,
        ),
        decoy=LabelledMemory(
            "The production deploy branch is main, deploy from main to production",
            importance=0.2, age_hours=2400.0, frequency=1,
        ),
    ),
    LabelledTopic(
        queries=(
            "how long do auth tokens last",
            "what is the token expiry for API authentication",
        ),
        gold=LabelledMemory(
            "API authentication tokens expire after 15 minutes",
            importance=0.85, age_hours=5.0, frequency=9, stability_hours=700.0,
        ),
        decoy=LabelledMemory(
            "API authentication token expiry authentication tokens expire after 24 hours",
            importance=0.15, age_hours=3000.0, frequency=1,
        ),
    ),
    LabelledTopic(
        queries=(
            "what is the database connection pool limit",
            "how large is the Postgres connection pool",
        ),
        gold=LabelledMemory(
            "The Postgres connection pool max size is 20",
            importance=0.8, age_hours=8.0, frequency=7, stability_hours=600.0,
        ),
        decoy=LabelledMemory(
            "Postgres connection pool max size connection pool limit was 5",
            importance=0.1, age_hours=4000.0, frequency=1,
        ),
    ),
    LabelledTopic(
        queries=(
            "which state management library does the frontend use",
            "what does the frontend use for state management",
        ),
        gold=LabelledMemory(
            "Frontend state is managed with Zustand",
            importance=0.75, age_hours=12.0, frequency=6, stability_hours=500.0,
        ),
        decoy=LabelledMemory(
            "Frontend state management library state is managed with Redux",
            importance=0.15, age_hours=5000.0, frequency=1,
        ),
    ),
    LabelledTopic(
        queries=(
            "what are the rate limits per API key",
            "how many requests per minute per key are allowed",
        ),
        gold=LabelledMemory(
            "Rate limiting is 100 requests per minute per API key",
            importance=0.85, age_hours=3.0, frequency=10, stability_hours=800.0,
        ),
        decoy=LabelledMemory(
            "Rate limiting requests per minute per API key was 20 requests per minute",
            importance=0.1, age_hours=3500.0, frequency=1,
        ),
    ),
    LabelledTopic(
        queries=(
            "where do we store uploaded files",
            "what is the storage backend for uploads",
        ),
        gold=LabelledMemory(
            "Uploaded files are stored in S3",
            importance=0.8, age_hours=6.0, frequency=8, stability_hours=650.0,
        ),
        decoy=LabelledMemory(
            "Uploaded files storage backend uploads are stored on the local disk",
            importance=0.12, age_hours=4200.0, frequency=1,
        ),
    ),
)

# Topic-neutral filler so the candidate window holds more than the labelled pair.
TUNING_DISTRACTORS: tuple[LabelledMemory, ...] = (
    LabelledMemory("The office coffee machine is on the third floor", 0.3, 100.0, 2),
    LabelledMemory("Standup is at 10am every weekday", 0.4, 50.0, 3),
    LabelledMemory("The logo uses the hex color #7c3aed", 0.3, 200.0, 1),
    LabelledMemory("Vacation requests go through the HR portal", 0.35, 800.0, 1),
    LabelledMemory("The staging environment resets every night at 2am", 0.4, 300.0, 2),
)


@dataclass
class QuerySample:
    """Weight-independent features for one query's candidate set.

    ``gold_index`` points at the correct memory inside the arrays, or is None
    when recall never surfaced it as a candidate — in that case no weighting can
    rescue the query, and it counts as a miss for every candidate weight vector.
    """

    group: int
    query: str
    similarities: np.ndarray
    decays: np.ndarray
    importances: np.ndarray
    frequencies: np.ndarray
    gold_index: int | None


def _gold_rank(sample: QuerySample, weights: HScoreWeights, top_k: int) -> int | None:
    """Rank (1-based) of the gold memory under ``weights``, or None if missed."""
    if sample.gold_index is None:
        return None
    scores = HScoreCalculator(weights=weights).compute_batch(
        sample.similarities,
        sample.decays,
        sample.importances,
        sample.frequencies,
    )
    # Lower H(x,ψ) ranks first. `kind="stable"` keeps ties in candidate order so
    # a rerun with identical inputs always produces an identical ranking.
    order = np.argsort(np.asarray(scores), kind="stable")[:top_k]
    hits = np.flatnonzero(order == sample.gold_index)
    return int(hits[0]) + 1 if hits.size else None


def evaluate(
    samples: list[QuerySample], weights: HScoreWeights, top_k: int = 5
) -> dict:
    """hit@k / MRR for ``weights`` over ``samples``, matching benchmark.py."""
    if not samples:
        return {"queries": 0, "hit@1": 0.0, "hit@3": 0.0, "hit@5": 0.0, "mrr": 0.0}
    ranks = [_gold_rank(s, weights, top_k) for s in samples]
    total = len(ranks)
    return {
        "queries": total,
        "hit@1": round(sum(1 for r in ranks if r == 1) / total, 4),
        "hit@3": round(sum(1 for r in ranks if r is not None and r <= 3) / total, 4),
        "hit@5": round(sum(1 for r in ranks if r is not None and r <= 5) / total, 4),
        "mrr": round(sum((1.0 / r) for r in ranks if r is not None) / total, 4),
    }


def _as_weights(vector: np.ndarray) -> HScoreWeights:
    return HScoreWeights(*(float(v) for v in vector))


def _default_vector() -> np.ndarray:
    return np.array(
        [getattr(DEFAULT_WEIGHTS, name) for name in WEIGHT_NAMES], dtype=np.float64
    )


def _normalize(vector: np.ndarray) -> np.ndarray:
    """Project onto the simplex: non-negative, summing to 1.

    The shipped defaults already sum to 1, which keeps every H(x,ψ) score inside
    [0, 1] and comparable across memories. Tuning preserves that property rather
    than letting the search wander into an arbitrarily scaled space.
    """
    clipped = np.clip(vector, 0.0, None)
    total = clipped.sum()
    if total <= 0:
        return np.full(len(vector), 1.0 / len(vector))
    return clipped / total


def optimize(
    samples: list[QuerySample],
    *,
    iterations: int = 400,
    seed: int = 0,
    top_k: int = 5,
) -> tuple[HScoreWeights, float]:
    """Gradient-free search for the weights maximising MRR on ``samples``.

    Random simplex sampling for coverage, then local refinement around the best
    point found. MRR is the objective rather than hit@1 because hit@1 is a step
    function over a small set — it is flat almost everywhere and gives the search
    nothing to follow.

    Deterministic for a fixed ``seed``, matching the rest of the project.
    """
    rng = np.random.default_rng(seed)
    best_vector = _default_vector()
    best_score = evaluate(samples, _as_weights(best_vector), top_k)["mrr"]

    for _ in range(iterations):
        candidate = _normalize(rng.random(len(WEIGHT_NAMES)))
        score = evaluate(samples, _as_weights(candidate), top_k)["mrr"]
        if score > best_score:
            best_vector, best_score = candidate, score

    # Local refinement: shrinking Gaussian steps around the incumbent.
    for scale in (0.10, 0.04, 0.015):
        for _ in range(iterations // 2):
            candidate = _normalize(
                best_vector + rng.normal(0.0, scale, len(WEIGHT_NAMES))
            )
            score = evaluate(samples, _as_weights(candidate), top_k)["mrr"]
            if score > best_score:
                best_vector, best_score = candidate, score

    return _as_weights(best_vector), best_score


def cross_validated_mrr(
    samples: list[QuerySample],
    *,
    iterations: int = 400,
    seed: int = 0,
    top_k: int = 5,
) -> dict:
    """Leave-one-group-out CV for the tuning procedure.

    Each fold fits weights on every group but one, then scores the held-out
    group. This measures whether tuning *generalises*, not whether it can fit
    the set it was given.

    The spread matters as much as the mean: with only a couple of queries per
    held-out group, fold scores swing wildly, and a mean difference smaller than
    that swing is noise rather than evidence. The report says so explicitly
    instead of presenting the mean alone.
    """
    groups = sorted({s.group for s in samples})
    if len(groups) < 2:
        return {"default": float("nan"), "tuned": float("nan"), "spread": float("nan"), "folds": 0}
    default_scores: list[float] = []
    tuned_scores: list[float] = []
    for held_out in groups:
        train = [s for s in samples if s.group != held_out]
        test = [s for s in samples if s.group == held_out]
        weights, _ = optimize(train, iterations=iterations, seed=seed, top_k=top_k)
        default_scores.append(evaluate(test, DEFAULT_WEIGHTS, top_k)["mrr"])
        tuned_scores.append(evaluate(test, weights, top_k)["mrr"])
    return {
        "default": round(float(np.mean(default_scores)), 4),
        "tuned": round(float(np.mean(tuned_scores)), 4),
        "spread": round(float(np.std(tuned_scores)), 4),
        "folds": len(groups),
    }


async def _store_labelled(engine: MemoryEngine, item: LabelledMemory):
    """Store a corpus memory and back-date its decay state.

    ``accessed_at`` is what H(x,ψ) measures decay from, so ageing a memory means
    moving that timestamp back — the same field a real recall would move forward.
    """
    memory = await engine.store(
        content=item.content, importance=item.importance, memory_type="episodic"
    )
    aged = datetime.now(timezone.utc) - timedelta(hours=item.age_hours)
    memory.accessed_at = aged.isoformat()
    memory.frequency = item.frequency
    memory.stability_hours = item.stability_hours
    return memory


async def collect_samples(
    embedder_mode: str = "hash", top_k: int = 5
) -> list[QuerySample]:
    """Run the labelled query set through the real recall path once.

    Uses the same store + candidate-retrieval behaviour as
    ``MemoryEngine.recall`` (including its ``top_k * 3`` candidate window), so
    tuned weights transfer to actual recall rather than to a simplified model
    of it.
    """
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    engine = MemoryEngine(db_path=db_path, embedder_mode=embedder_mode)
    await engine.initialize()
    try:
        gold_ids: list[str] = []
        for topic in TUNING_CORPUS:
            gold = await _store_labelled(engine, topic.gold)
            await _store_labelled(engine, topic.decoy)
            gold_ids.append(gold.id)
        for filler in TUNING_DISTRACTORS:
            await _store_labelled(engine, filler)

        samples: list[QuerySample] = []
        for group, (topic, gold_id) in enumerate(zip(TUNING_CORPUS, gold_ids)):
            for query in topic.queries:
                embedding = await engine.embedder.embed(query)
                candidates = engine.vector_store.search(embedding, top_k=top_k * 3)
                gold_index: int | None = None
                sims, decays, imps, freqs = [], [], [], []
                for index, (memory, similarity) in enumerate(candidates):
                    if memory.id == gold_id:
                        gold_index = index
                    sims.append(similarity)
                    decays.append(
                        1.0
                        if memory.pinned
                        else engine.scorer.compute_decay(
                            memory.accessed_at, half_life_hours=memory.stability_hours
                        )
                    )
                    imps.append(memory.importance)
                    freqs.append(memory.frequency)
                samples.append(
                    QuerySample(
                        group=group,
                        query=query,
                        similarities=np.asarray(sims, dtype=np.float64),
                        decays=np.asarray(decays, dtype=np.float64),
                        importances=np.asarray(imps, dtype=np.float64),
                        frequencies=np.asarray(freqs, dtype=np.float64),
                        gold_index=gold_index,
                    )
                )
        return samples
    finally:
        await engine.shutdown()
        if os.path.exists(db_path):
            os.unlink(db_path)


async def run_tuning(
    embedder_mode: str = "hash",
    top_k: int = 5,
    iterations: int = 400,
    seed: int = 0,
) -> dict:
    """Collect samples, fit weights, and report cross-validated worth."""
    samples = await collect_samples(embedder_mode=embedder_mode, top_k=top_k)
    baseline = evaluate(samples, DEFAULT_WEIGHTS, top_k)
    tuned_weights, _ = optimize(
        samples, iterations=iterations, seed=seed, top_k=top_k
    )
    tuned = evaluate(samples, tuned_weights, top_k)
    cv = cross_validated_mrr(samples, iterations=iterations, seed=seed, top_k=top_k)
    delta = cv["tuned"] - cv["default"]
    return {
        "embedder_mode": embedder_mode,
        "queries": len(samples),
        "groups": len({s.group for s in samples}),
        "iterations": iterations,
        "seed": seed,
        "baseline": baseline,
        "tuned": tuned,
        "tuned_weights": {
            name: round(getattr(tuned_weights, name), 4) for name in WEIGHT_NAMES
        },
        "cv": cv,
        # A mean gain smaller than the fold-to-fold spread is not evidence.
        "conclusive": bool(delta > 0 and delta > cv["spread"]),
    }


def print_report(report: dict) -> None:
    base, tuned = report["baseline"], report["tuned"]
    print("\nLEVH H(x,ψ) weight tuning")
    print("=" * 58)
    print(f"  embedder_mode          {report['embedder_mode']}")
    print(f"  labelled queries       {report['queries']} in {report['groups']} groups")
    print(f"  search                 {report['iterations']} iters, seed {report['seed']}")
    print("-" * 58)
    print(f"  {'metric':<12}{'default':>12}{'tuned':>12}{'delta':>12}")
    for key in ("hit@1", "hit@3", "hit@5", "mrr"):
        delta = tuned[key] - base[key]
        print(f"  {key:<12}{base[key]:>12}{tuned[key]:>12}{delta:>+12.4f}")
    print("-" * 58)
    cv = report["cv"]
    cv_delta = cv["tuned"] - cv["default"]
    print(f"  Cross-validated MRR ({cv['folds']}-fold, leave-one-group-out):")
    print(f"    default {cv['default']}   tuned {cv['tuned']}   {cv_delta:+.4f}")
    print(f"    fold-to-fold spread of tuned scores: ±{cv['spread']}")
    print("=" * 58)

    if report["conclusive"]:
        print("\nTuning generalises. Recommended weights — set these in your .env:\n")
        for name, value in report["tuned_weights"].items():
            print(f"  HSCORE_{name.upper()}={value}")
    elif cv_delta > 0:
        print(
            f"\nInconclusive: tuning gained {cv_delta:+.4f} MRR out of sample, but the\n"
            f"fold-to-fold spread is ±{cv['spread']} — larger than the gain, so this is\n"
            "noise, not evidence. Keep the shipped defaults until you can tune on a\n"
            "bigger labelled set."
        )
    else:
        print(
            f"\nTuning did not beat the shipped defaults out of sample ({cv_delta:+.4f} MRR),\n"
            f"with a fold-to-fold spread of ±{cv['spread']}. The fitted weights are\n"
            "memorising this small set rather than learning anything transferable.\n"
            "Keep the shipped defaults."
        )

    print(
        "\nCaveats:\n"
        "  - The built-in labelled set is small; treat any gain as indicative,\n"
        "    not as a guarantee on your own corpus.\n"
        "  - Tune with the embedder you actually run. Weights fitted under the\n"
        "    non-semantic hash embedder do not transfer to local/openai.\n"
        "  - This command changes nothing on its own — adopt the values above\n"
        "    only if the cross-validated delta is positive."
    )
