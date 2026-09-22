"""In-process Prometheus metrics (issue #145).

The server had no way to say what it was doing: the only observability was a
plain-text log line, so a hot loop or a degraded embedder was invisible until a
user complained. This module is the smallest honest counter of the things the
issue named — recall/store latency, embedder fallback, admission verdicts,
derived-rebuild failures and DB lock-wait — exposed at ``/api/metrics`` in the
Prometheus text era.

Deliberately dependency-free. ``prometheus_client`` would bring a second
registry, its own exposition server and a multiprocess mode the single-process
server does not need; the exposition format is a few lines of text and one
histogram, so the code here stays the whole story. It is also deliberately
*global*: the counters describe one process, and the engine, the routes and the
middleware all live in that process.
"""

from __future__ import annotations

import threading

# One lock for every metric: the counters are touched once per request, not
# per memory, so contention is not a thing to optimize for, and a single lock
# keeps a render from ever seeing a half-updated histogram.
_LOCK = threading.Lock()

#: Histogram buckets in seconds. Local SQLite work sits in the low
#: milliseconds; the tail reaches seconds when the embedder is cold or a
#: rebuild holds the write lock.
_LATENCY_BUCKETS: tuple[float, ...] = (
    0.001,
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)

#: Buckets for the DB lock-wait histogram. A writer holding ``BEGIN
#: IMMEDIATE`` blocks a peer for as long as its own work takes; the useful
#: questions are "did anyone wait" and "was it milliseconds or the busy
#: timeout", not the sub-millisecond shape of an uncontended acquire.
LOCK_WAIT_BUCKETS: tuple[float, ...] = (
    0.001,
    0.01,
    0.05,
    0.1,
    0.5,
    1.0,
    2.5,
    5.0,
)

#: Histogram name -> bucket bounds. The renderer needs the bounds to emit the
#: ``_bucket`` lines, and the default latency buckets were the only set until
#: the lock-wait histogram needed a much shorter scale (it is bounded by the
#: SQLite busy timeout, not by embedder work).
_BUCKETS: dict[str, tuple[float, ...]] = {
    "levh_recall_latency_seconds": _LATENCY_BUCKETS,
    "levh_store_latency_seconds": _LATENCY_BUCKETS,
    "levh_db_lock_wait_seconds": LOCK_WAIT_BUCKETS,
}

_HELP: dict[str, str] = {
    "levh_recall_latency_seconds": "Recall request latency in seconds.",
    "levh_store_latency_seconds": "Memory store request latency in seconds.",
    "levh_embedder_fallback_total": (
        "Embedder resolutions that fell back to the hash implementation."
    ),
    "levh_derived_rebuild_total": "Derived-state rebuild passes, by outcome.",
    "levh_admission_verdict_total": "Admission gate verdicts, by decision.",
    "levh_memory_rows_quarantined_total": (
        "Stored memory rows rejected by the model and skipped at read time."
    ),
    "levh_db_lock_wait_seconds": (
        "Time spent waiting for the SQLite write lock before the busy timeout."
    ),
}

# name -> label pairs -> value
_counters: dict[str, dict[tuple[tuple[str, str], ...], float]] = {}
# name -> label pairs -> [per-bucket counts..., +Inf implied, sum, count]
_histograms: dict[str, dict[tuple[tuple[str, str], ...], list[float]]] = {}


def _label_key(labels: dict[str, object]) -> tuple[tuple[str, str], ...]:
    return tuple((str(k), str(v)) for k, v in labels.items())


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _render_labels(key: tuple[tuple[str, str], ...]) -> str:
    if not key:
        return ""
    inner = ",".join(f'{name}="{_escape(value)}"' for name, value in key)
    return "{" + inner + "}"


def _format(value: float) -> str:
    # Integers as integers keeps `levh_x_total 3` rather than `3.0`; Prometheus
    # accepts both, but the former is what every dashboard expects to see.
    if value == int(value):
        return str(int(value))
    return repr(value)


def inc(name: str, amount: float = 1.0, **labels: object) -> None:
    """Increment counter *name* (creating its series on first use)."""
    key = _label_key(labels)
    with _LOCK:
        bucket = _counters.setdefault(name, {})
        bucket[key] = bucket.get(key, 0.0) + amount


def observe(name: str, value: float, **labels: object) -> None:
    """Record *value* in histogram *name* (creating its series on first use)."""
    key = _label_key(labels)
    buckets = _BUCKETS.get(name, _LATENCY_BUCKETS)
    with _LOCK:
        by_labels = _histograms.setdefault(name, {})
        state = by_labels.get(key)
        if state is None:
            state = [0.0] * len(buckets) + [0.0, 0.0]
            by_labels[key] = state
        for index, bound in enumerate(buckets):
            if value <= bound:
                state[index] += 1.0
        state[-2] += value
        state[-1] += 1.0


def render() -> str:
    """The registry as Prometheus text exposition (deterministic order)."""
    lines: list[str] = []
    with _LOCK:
        for name in sorted(_counters):
            lines.extend(_render_counter(name, _counters[name]))
        for name in sorted(_histograms):
            lines.extend(_render_histogram(name, _histograms[name]))
    return "\n".join(lines) + "\n"


def _render_counter(
    name: str, series: dict[tuple[tuple[str, str], ...], float]
) -> list[str]:
    out = _render_header(name, "counter")
    for key in sorted(series):
        out.append(f"{name}{_render_labels(key)} {_format(series[key])}")
    return out


def _render_histogram(
    name: str, series: dict[tuple[tuple[str, str], ...], list[float]]
) -> list[str]:
    out = _render_header(name, "histogram")
    buckets = _BUCKETS.get(name, _LATENCY_BUCKETS)
    for key in sorted(series):
        state = series[key]
        for index, bound in enumerate(buckets):
            labels = key + (("le", repr(bound)),)
            out.append(f"{name}_bucket{_render_labels(labels)} {_format(state[index])}")
        inf = key + (("le", "+Inf"),)
        out.append(f"{name}_bucket{_render_labels(inf)} {_format(state[-1])}")
        out.append(f"{name}_sum{_render_labels(key)} {_format(state[-2])}")
        out.append(f"{name}_count{_render_labels(key)} {_format(state[-1])}")
    return out


def _render_header(name: str, kind: str) -> list[str]:
    out = []
    help_text = _HELP.get(name)
    if help_text:
        out.append(f"# HELP {name} {help_text}")
    out.append(f"# TYPE {name} {kind}")
    return out


def reset() -> None:
    """Drop every series. Test-facing: one registry per process otherwise
    carries counts between tests and makes assertions order-dependent."""
    with _LOCK:
        _counters.clear()
        _histograms.clear()
