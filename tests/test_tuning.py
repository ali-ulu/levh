"""Tests for the offline H(x,ψ) weight tuner (`levh tune`)."""

from __future__ import annotations

import numpy as np
import pytest

from server.core.hscore import DEFAULT_WEIGHTS, HScoreWeights
from server.core.tuning import (
    WEIGHT_NAMES,
    QuerySample,
    _normalize,
    collect_samples,
    cross_validated_mrr,
    evaluate,
    optimize,
    run_tuning,
)


def _sample(group: int, gold_index: int | None, n: int = 4) -> QuerySample:
    """A synthetic candidate set with deliberately varied features."""
    return QuerySample(
        group=group,
        query=f"q{group}",
        similarities=np.linspace(0.9, 0.6, n),
        decays=np.linspace(0.1, 1.0, n),
        importances=np.linspace(0.1, 0.9, n),
        frequencies=np.arange(1, n + 1, dtype=float),
        gold_index=gold_index,
    )


def test_normalize_projects_onto_simplex():
    out = _normalize(np.array([2.0, 1.0, 1.0, 0.0]))
    assert pytest.approx(out.sum()) == 1.0
    assert (out >= 0).all()
    # Negative components are clipped, not reflected.
    assert (_normalize(np.array([-5.0, 1.0, 0.0, 0.0])) >= 0).all()
    # A degenerate all-zero vector falls back to uniform rather than dividing by 0.
    assert pytest.approx(_normalize(np.zeros(4)).tolist()) == [0.25] * 4


def test_evaluate_scores_a_perfect_and_a_missing_gold():
    # Highest similarity, freshest, most important, most frequent → always rank 1.
    perfect = QuerySample(
        group=0,
        query="q",
        similarities=np.array([1.0, 0.1]),
        decays=np.array([1.0, 0.1]),
        importances=np.array([1.0, 0.1]),
        frequencies=np.array([100.0, 1.0]),
        gold_index=0,
    )
    assert evaluate([perfect], DEFAULT_WEIGHTS)["mrr"] == 1.0

    # A gold memory that never entered the candidate set is a miss under any weights.
    missing = _sample(0, gold_index=None)
    assert evaluate([missing], DEFAULT_WEIGHTS)["mrr"] == 0.0
    assert evaluate([missing], HScoreWeights(0.9, 0.05, 0.03, 0.02))["mrr"] == 0.0


def test_evaluate_handles_an_empty_sample_set():
    assert evaluate([], DEFAULT_WEIGHTS)["queries"] == 0


def test_optimize_is_deterministic_for_a_fixed_seed():
    samples = [_sample(g, gold_index=g % 3) for g in range(4)]
    first, first_score = optimize(samples, iterations=40, seed=7)
    second, second_score = optimize(samples, iterations=40, seed=7)
    assert first == second
    assert first_score == second_score
    # Weights stay on the simplex the defaults live on.
    assert pytest.approx(sum(getattr(first, n) for n in WEIGHT_NAMES)) == 1.0


def test_optimize_never_returns_worse_than_the_defaults_in_sample():
    """The search starts from the shipped weights, so it cannot regress on the
    set it was fitted to — any recommendation is at least as good in-sample."""
    samples = [_sample(g, gold_index=g % 3) for g in range(4)]
    weights, score = optimize(samples, iterations=60, seed=1)
    assert score >= evaluate(samples, DEFAULT_WEIGHTS)["mrr"]


def test_cross_validation_needs_at_least_two_groups():
    single = cross_validated_mrr([_sample(0, gold_index=0)], iterations=5)
    assert single["folds"] == 0


@pytest.mark.asyncio
async def test_collected_features_actually_vary():
    """Regression guard for a defect that made tuning meaningless.

    `benchmark.DATASET` stores every memory fresh with the same importance and
    frequency, so decay/importance/frequency were identical across candidates —
    three of the four H(x,ψ) terms became the same constant for every candidate
    and could not reorder anything. If the tuning corpus ever degrades that way
    again, the tuner would silently have nothing to fit.
    """
    samples = await collect_samples(embedder_mode="hash")
    assert samples, "tuning corpus produced no samples"
    for field in ("similarities", "decays", "importances", "frequencies"):
        spreads = [float(np.std(getattr(s, field))) for s in samples]
        assert max(spreads) > 0.0, f"{field} is constant across all candidates"


@pytest.mark.asyncio
async def test_weights_can_actually_reorder_the_ranking():
    """Different weights must produce different rankings on this corpus,
    otherwise the tuner is searching a space with no effect on the outcome."""
    samples = await collect_samples(embedder_mode="hash")
    similarity_only = evaluate(samples, HScoreWeights(1.0, 0.0, 0.0, 0.0))
    decay_heavy = evaluate(samples, HScoreWeights(0.1, 0.7, 0.1, 0.1))
    assert similarity_only != decay_heavy


@pytest.mark.asyncio
async def test_run_tuning_reports_cross_validation_and_leaves_defaults_alone():
    report = await run_tuning(embedder_mode="hash", iterations=20, seed=0)

    assert report["groups"] >= 2
    assert set(report["tuned_weights"]) == set(WEIGHT_NAMES)
    assert pytest.approx(sum(report["tuned_weights"].values()), abs=1e-6) == 1.0

    cv = report["cv"]
    assert cv["folds"] == report["groups"]
    # A recommendation is only "conclusive" when the out-of-sample gain exceeds
    # the fold-to-fold spread — a smaller gain is noise.
    assert report["conclusive"] == bool(
        cv["tuned"] - cv["default"] > 0 and cv["tuned"] - cv["default"] > cv["spread"]
    )

    # Tuning is offline analysis: the process-wide defaults must be untouched.
    assert DEFAULT_WEIGHTS.alpha == 0.4
    assert DEFAULT_WEIGHTS.beta == 0.2
    assert DEFAULT_WEIGHTS.gamma == 0.3
    assert DEFAULT_WEIGHTS.delta == 0.1
