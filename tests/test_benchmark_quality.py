"""The recall-quality regression gate.

Every unit test can pass while ranking quality quietly drops — a weight tweak
or a candidate-source change moves hit@k without breaking an assertion. This
runs the labelled corpus on the real pipeline and fails on a gated metric below
its floor, so a regression is caught where it is introduced.

Only the model-free (``hash``) mode is gated: it is deterministic — no model, no
network, no platform-specific arithmetic — so the same corpus yields the same
numbers on every runner. A semantic mode's numbers depend on the installed
model and are not gated here.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ["EMBEDDER_MODE"] = "hash"

from server.core.benchmark import (
    GATED_METRICS,
    QUALITY_FLOORS,
    quality_failures,
    run_benchmark,
)

ROOT = Path(__file__).resolve().parents[1]

_HEALTHY = {"embedder_mode": "hash", "hit@1": 1.0, "hit@3": 1.0, "hit@5": 1.0, "mrr": 1.0}
_REGRESSED = {**_HEALTHY, "hit@1": 0.0}


@pytest.fixture(scope="module")
def metrics():
    import asyncio

    return asyncio.run(run_benchmark("hash", top_k=5))


def test_every_gated_metric_clears_its_floor(metrics):
    failures = quality_failures(metrics)
    assert failures == [], "recall quality regressed:\n" + "\n".join(failures)


def test_floors_cover_the_metrics_that_carry_a_quality_signal():
    """A floor set that drifts from the report would gate nothing.

    Every declared metric must be one the harness actually produces, or the
    check silently passes on a missing key.
    """
    assert set(QUALITY_FLOORS["hash"]) == set(GATED_METRICS)
    assert set(GATED_METRICS) <= {"hit@1", "hit@3", "hit@5", "mrr"}


def test_the_corpus_can_actually_fail(metrics):
    """A gate whose corpus scores 1.0 everywhere cannot observe a regression.

    The floors are the corpus's own achieved numbers; if the corpus is easy
    enough that they reach 1.0, every gated metric passes no matter how the
    ranking changes. At least one gated metric must sit strictly below 1.0.
    """
    assert any(value < 1.0 for value in metrics.values() if isinstance(value, float)), (
        "every metric is 1.0 — the corpus cannot fail, so the gate is decorative"
    )


def test_the_corpus_has_near_miss_distractors():
    """The distractor set must include rows that share a query's vocabulary but
    answer a different question, or it is trivially separable."""
    from server.core.benchmark import DISTRACTORS

    assert any("staging" in row and "deploy" in row for row in DISTRACTORS)
    assert any("password" in row for row in DISTRACTORS)


def test_a_metric_below_its_floor_is_reported(metrics):
    """The gate must fail closed: a dropped metric names the failure, and a
    missing one is not treated as passing."""
    regressed = dict(metrics, **{"hit@1": 0.0})
    assert any("hit@1" in f for f in quality_failures(regressed))

    broken = {k: v for k, v in metrics.items() if k != "mrr"}
    assert any("mrr missing" in f for f in quality_failures(broken))


def test_an_ungated_mode_is_not_judged_by_another_modes_floor():
    """A semantic run's numbers depend on the installed model; gating them on
    the hash floors would fail on a machine that simply has a different model."""
    assert quality_failures({"embedder_mode": "openai", "hit@1": 0.0}) == []


def test_ci_runs_the_gate():
    """The gate only regresses if CI stops calling it — pin the step."""
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "levh benchmark --embedder-mode hash --check" in ci


def test_cli_check_exits_non_zero_on_a_regression(monkeypatch, capsys):
    """The CLI wiring, not just the helper: `--check` must turn a failed gate
    into a non-zero exit so CI can act on it."""
    from server.commands import quality

    class _Args:
        embedder_mode = "hash"
        top_k = 5
        check = True

    async def _regressed(**_):
        return {
            "embedder_mode": "hash",
            "hit@1": 0.0,
            "hit@3": 1.0,
            "hit@5": 1.0,
            "mrr": 1.0,
        }

    monkeypatch.setattr("server.core.benchmark.run_benchmark", _regressed)
    assert quality.cmd_benchmark(_Args()) == 1
    assert "FAILED" in capsys.readouterr().out


def test_cli_check_passes_on_a_healthy_run(monkeypatch):
    from server.commands import quality

    class _Args:
        embedder_mode = "hash"
        top_k = 5
        check = True

    async def _healthy(**_):
        return {
            "embedder_mode": "hash",
            "hit@1": 1.0,
            "hit@3": 1.0,
            "hit@5": 1.0,
            "mrr": 1.0,
        }

    monkeypatch.setattr("server.core.benchmark.run_benchmark", _healthy)
    assert quality.cmd_benchmark(_Args()) == 0


def _fake_run(metrics: dict):
    async def _run(*_, **__):
        return metrics

    return _run


def test_module_main_reports_a_failed_gate(monkeypatch, capsys):
    """`python -m server.core.benchmark --check` is entry point a different CI
    or a user invokes without the console script; it must fail closed."""
    from server.core import benchmark

    monkeypatch.setattr(benchmark, "run_benchmark", _fake_run(_REGRESSED))
    monkeypatch.setattr(sys, "argv", ["benchmark", "--embedder", "hash", "--check"])

    assert benchmark.main() == 1
    out = capsys.readouterr().out
    assert "Quality gate failed" in out and "hit@1" in out


def test_module_main_reports_a_passed_gate(monkeypatch, capsys):
    from server.core import benchmark

    monkeypatch.setattr(benchmark, "run_benchmark", _fake_run(_HEALTHY))
    monkeypatch.setattr(sys, "argv", ["benchmark", "--embedder", "hash", "--check"])

    assert benchmark.main() == 0
    assert "Quality gate passed for hash" in capsys.readouterr().out


def test_module_main_says_nothing_to_gate_for_an_ungated_mode(monkeypatch, capsys):
    from server.core import benchmark

    monkeypatch.setattr(
        benchmark, "run_benchmark", _fake_run({**_HEALTHY, "embedder_mode": "openai"})
    )
    monkeypatch.setattr(sys, "argv", ["benchmark", "--embedder", "openai", "--check"])

    assert benchmark.main() == 0
    assert "nothing to gate" in capsys.readouterr().out


def test_module_main_without_check_never_gates(monkeypatch):
    from server.core import benchmark

    monkeypatch.setattr(benchmark, "run_benchmark", _fake_run(_REGRESSED))
    monkeypatch.setattr(sys, "argv", ["benchmark", "--embedder", "hash"])

    assert benchmark.main() == 0


def test_cli_without_check_never_gates(monkeypatch):
    """Plain `levh benchmark` stays a report — `--check` is opt-in, so an
    existing workflow that reads the numbers is unaffected."""
    from server.commands import quality

    class _Args:
        embedder_mode = "hash"
        top_k = 5
        check = False

    monkeypatch.setattr("server.core.benchmark.run_benchmark", _fake_run(_REGRESSED))
    assert quality.cmd_benchmark(_Args()) == 0


def test_cli_check_says_nothing_to_gate_for_an_ungated_mode(monkeypatch, capsys):
    from server.commands import quality

    class _Args:
        embedder_mode = "openai"
        top_k = 5
        check = True

    monkeypatch.setattr(
        "server.core.benchmark.run_benchmark", _fake_run({**_HEALTHY, "embedder_mode": "openai"})
    )
    assert quality.cmd_benchmark(_Args()) == 0
    assert "nothing to gate" in capsys.readouterr().out
