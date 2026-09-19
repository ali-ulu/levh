"""In-process Prometheus metrics and their exposition endpoint (issue #145).

The registry is process-global, so every test resets it first: a counter left
by an earlier test makes an assertion order-dependent. The endpoint is checked
against the real app so the route, its versioned alias and the text format are
exercised together rather than asserted from the registry alone.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.core import metrics


@pytest.fixture(autouse=True)
def _clean_registry():
    metrics.reset()
    yield
    metrics.reset()


def _client() -> TestClient:
    from server.api import app

    # A loopback peer keeps the remote-access boundary out of the way.
    return TestClient(app, client=("127.0.0.1", 51234))


# ── The registry ─────────────────────────────────────────────────────


def test_counters_render_prometheus_text():
    metrics.inc("levh_recall_latency_seconds_total", outcome="hit")
    metrics.inc("levh_recall_latency_seconds_total", outcome="hit")
    metrics.inc("levh_recall_latency_seconds_total", outcome="miss")

    text = metrics.render()

    assert "# TYPE levh_recall_latency_seconds_total counter" in text
    assert 'levh_recall_latency_seconds_total{outcome="hit"} 2' in text
    assert 'levh_recall_latency_seconds_total{outcome="miss"} 1' in text


def test_histogram_renders_buckets_sum_and_count():
    metrics.observe("levh_store_latency_seconds", 0.02)
    metrics.observe("levh_store_latency_seconds", 3.0)

    text = metrics.render()

    assert "# TYPE levh_store_latency_seconds histogram" in text
    assert 'levh_store_latency_seconds_bucket{le="0.01"} 0' in text
    assert 'levh_store_latency_seconds_bucket{le="0.025"} 1' in text
    assert 'levh_store_latency_seconds_bucket{le="+Inf"} 2' in text
    assert "levh_store_latency_seconds_count 2" in text
    assert "levh_store_latency_seconds_sum 3.02" in text


def test_render_is_deterministic_and_empty_registry_is_blank():
    metrics.inc("b_total")
    metrics.inc("a_total")

    assert metrics.render() == metrics.render()
    assert metrics.render().index("a_total") < metrics.render().index("b_total")

    metrics.reset()
    assert metrics.render() == "\n"


def test_metric_names_and_labels_are_escapable():
    metrics.inc("levh_weird_total", note='a"b\\c')

    text = metrics.render()
    assert 'note="a\\"b\\\\c"' in text


# ── The endpoint ─────────────────────────────────────────────────────


def test_metrics_endpoint_exposes_the_registry():
    metrics.inc("levh_embedder_fallback_total", reason="test")

    with _client() as client:
        response = client.get("/api/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert 'levh_embedder_fallback_total{reason="test"} 1' in response.text


def test_metrics_is_served_under_the_versioned_contract_too():
    with _client() as client:
        assert client.get("/api/v1/metrics").status_code == 200


# ── Instrumentation ──────────────────────────────────────────────────


def test_rebuild_failure_increments_the_counter(monkeypatch):
    from server.core.memory_engine import MemoryEngine

    engine = MemoryEngine(embedder_mode="hash")

    async def _boom() -> None:
        raise RuntimeError("rebuild exploded")

    monkeypatch.setattr(engine, "reindex_entities", _boom)

    async def _run() -> None:
        await engine.initialize()
        # Inline (retry=False) so the failing pass fails fast instead of
        # sleeping through the background backoff schedule.
        await engine._rebuild_derived(retry=False)
        await engine.shutdown()

    with pytest.raises(RuntimeError):
        asyncio.run(_run())

    assert 'levh_derived_rebuild_total{outcome="failure"} 1' in metrics.render()


def test_docker_healthcheck_reads_metrics():
    """The probe must be wired to the endpoint the code actually serves."""
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert "/api/health" in dockerfile
    assert "/api/metrics" in dockerfile