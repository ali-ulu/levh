"""H(x,ψ) Scoring Algorithm — Hybrid memory relevance scoring.

H(x,ψ) = α·(1-similarity) + β·(1-decay_factor) + γ·(1-importance) + δ·(1-frequency_norm)

Score 0 = perfect relevance, Score 1 = irrelevant.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


@dataclass
class HScoreWeights:
    """Weights for the H(x,ψ) scoring formula."""

    alpha: float = 0.4   # similarity weight (low similarity = high penalty)
    beta: float = 0.2    # temporal decay weight
    gamma: float = 0.3   # importance weight (low importance = high penalty)
    delta: float = 0.1   # frequency weight (rare access = slight penalty)

    @classmethod
    def from_env(cls) -> "HScoreWeights":
        """Build weights from HSCORE_* environment variables (documented in .env.example)."""
        return cls(
            alpha=_env_float("HSCORE_ALPHA", 0.4),
            beta=_env_float("HSCORE_BETA", 0.2),
            gamma=_env_float("HSCORE_GAMMA", 0.3),
            delta=_env_float("HSCORE_DELTA", 0.1),
        )


DEFAULT_WEIGHTS = HScoreWeights()

DEFAULT_HALF_LIFE_HOURS = 168.0  # 1 week
DEFAULT_REINFORCEMENT_GAIN = 0.5  # each recall multiplies stability by up to 1.75x
DEFAULT_MAX_STABILITY_HOURS = 8760.0  # 1 year — a well-reinforced memory nearly never forgotten


class HScoreCalculator:
    """Computes H(x,ψ) scores for memory relevance.

    Models memory the way human memory actually behaves, not just a fixed
    exponential decay:
      - Every memory has its OWN half-life ("stability"), not a global one.
      - Recalling a memory resets its decay clock (measured from last
        access, not creation) AND reinforces it — like spaced repetition /
        the testing effect, each successful recall makes a memory more
        durable, weighted by how important it is (emotional salience).
      - Pinned memories are permanent (like deeply encoded core facts).
    """

    def __init__(
        self,
        weights: HScoreWeights | None = None,
        half_life_hours: float | None = None,
        reinforcement_gain: float | None = None,
        max_stability_hours: float | None = None,
    ):
        self.w = weights or HScoreWeights.from_env()
        self.half_life_hours = (
            half_life_hours
            if half_life_hours is not None
            else _env_float("DECAY_HALF_LIFE_HOURS", DEFAULT_HALF_LIFE_HOURS)
        )
        self.reinforcement_gain = (
            reinforcement_gain
            if reinforcement_gain is not None
            else _env_float("REINFORCEMENT_GAIN", DEFAULT_REINFORCEMENT_GAIN)
        )
        self.max_stability_hours = (
            max_stability_hours
            if max_stability_hours is not None
            else _env_float("MAX_STABILITY_HOURS", DEFAULT_MAX_STABILITY_HOURS)
        )

    def compute(
        self,
        similarity: float,
        decay_factor: float,
        importance: float,
        frequency: int,
        max_frequency: int = 100,
    ) -> float:
        """Compute H(x,ψ) for a single memory.

        Args:
            similarity:   Cosine similarity to query (0-1).
            decay_factor: Time-based decay (0-1, 1=fresh, 0=stale).
            importance:   User-assigned importance (0-1).
            frequency:    Number of times accessed.
            max_frequency: Normalisation ceiling for frequency.
        """
        freq_norm = min(frequency / max(max_frequency, 1), 1.0)
        score = (
            self.w.alpha * (1.0 - similarity)
            + self.w.beta * (1.0 - decay_factor)
            + self.w.gamma * (1.0 - importance)
            + self.w.delta * (1.0 - freq_norm)
        )
        return round(float(score), 6)

    def compute_batch(
        self,
        similarities: list[float] | np.ndarray,
        decay_factors: list[float] | np.ndarray,
        importances: list[float] | np.ndarray,
        frequencies: list[int] | np.ndarray,
        max_frequency: int = 100,
    ) -> list[float]:
        """Vectorised H(x,ψ) computation using NumPy."""
        sims = np.asarray(similarities, dtype=np.float64)
        decays = np.asarray(decay_factors, dtype=np.float64)
        imps = np.asarray(importances, dtype=np.float64)
        freqs = np.minimum(np.asarray(frequencies, dtype=np.float64) / max(max_frequency, 1), 1.0)

        scores = (
            self.w.alpha * (1.0 - sims)
            + self.w.beta * (1.0 - decays)
            + self.w.gamma * (1.0 - imps)
            + self.w.delta * (1.0 - freqs)
        )
        return [round(float(s), 6) for s in scores]

    def compute_decay(
        self,
        since: str,
        as_of: str | None = None,
        half_life_hours: float | None = None,
    ) -> float:
        """Exponential time-decay: decay = 0.5^(elapsed / half_life).

        Elapsed time is measured from the memory's LAST ACCESS, not its
        creation — recalling a memory resets its clock, exactly like
        remembering something makes it feel fresh again.

        Args:
            since: ISO timestamp the clock resets from (memory.accessed_at).
            as_of: ISO timestamp to measure decay at (defaults to now).
            half_life_hours: Hours until score halves. Defaults to the
                calculator's global half-life, but callers should pass the
                memory's own `stability_hours` so reinforced memories decay
                slower than fresh ones.
        """
        if half_life_hours is None:
            half_life_hours = self.half_life_hours
        try:
            start = datetime.fromisoformat(since)
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            end = datetime.fromisoformat(as_of) if as_of else datetime.now(timezone.utc)
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            hours = abs((end - start).total_seconds()) / 3600.0
            decay = 0.5 ** (hours / half_life_hours)
        except (ValueError, TypeError):
            decay = 1.0
        return round(float(decay), 6)

    def reinforce(self, stability_hours: float, importance: float) -> float:
        """Strengthen a memory's stability after a successful recall.

        Modeled on spaced-repetition scheduling (SM-2/FSRS style): each
        recall multiplies stability by (1 + gain·(0.5+importance)), so
        important memories consolidate into long-term durability faster
        than trivial ones — the same way emotionally salient events are
        remembered more easily than mundane ones.
        """
        growth = 1.0 + self.reinforcement_gain * (0.5 + max(0.0, min(1.0, importance)))
        return round(min(stability_hours * growth, self.max_stability_hours), 4)

    def weaken(self, stability_hours: float, factor: float | None = None) -> float:
        """Weaken a memory's stability — the inverse of reinforce.

        Used when a memory turns out to be wrong or stale (negative
        feedback), or when a newer, near-identical memory supersedes it
        (retroactive interference). The floor of 1 hour keeps the memory
        recallable long enough to be reviewed or deleted rather than
        vanishing instantly.
        """
        if factor is None:
            factor = _env_float("FEEDBACK_WEAKEN_FACTOR", 0.5)
        factor = max(0.05, min(1.0, factor))
        return round(max(stability_hours * factor, 1.0), 4)

    def retention_curve(
        self, stability_hours: float, days: int = 30, points: int = 30
    ) -> list[dict]:
        """Predicted retention (0-1) at evenly spaced points over the next
        `days`, given the memory's current stability. Powers the "forgetting
        curve" visualization — the same curve shape as Ebbinghaus's original
        1885 forgetting-curve experiments, but per-memory and reinforceable."""
        step = max(days, 1) / max(points, 1)
        curve = []
        for i in range(points + 1):
            day = round(i * step, 2)
            retention = 0.5 ** ((day * 24.0) / max(stability_hours, 1e-6))
            curve.append({"day": day, "retention": round(float(retention), 4)})
        return curve

    def breakdown(
        self,
        similarity: float,
        decay_factor: float,
        importance: float,
        frequency: int,
        max_frequency: int = 100,
    ) -> dict:
        """Return individual score components for visualization."""
        freq_norm = min(frequency / max(max_frequency, 1), 1.0)
        return {
            "alpha_component": round(self.w.alpha * (1.0 - similarity), 6),
            "beta_component": round(self.w.beta * (1.0 - decay_factor), 6),
            "gamma_component": round(self.w.gamma * (1.0 - importance), 6),
            "delta_component": round(self.w.delta * (1.0 - freq_norm), 6),
        }
