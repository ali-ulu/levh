"""System routes — stats, config, health, benchmark."""

from __future__ import annotations


from fastapi import Depends, APIRouter, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse

from server.core import llm_policy, metrics
from server.core.embedder import probe_embedder_status
from server.auth import unauthenticated_remote_access_enabled
from server.core.runtime_config import configured_bind_host
from server.core.types import MemoryStats
from server.routes.deps import get_engine
from server.routes.deps import APP_VERSION, api_token, logger
from server.routes.models import BenchmarkResponse, HealthResponse, ReadyzResponse, ServerConfigResponse

router = APIRouter()


@router.get("/api/stats", response_model=MemoryStats)
async def get_stats(engine=Depends(get_engine)):
    stats = await engine.get_stats()
    return stats.model_dump()


@router.get("/api/config", response_model=ServerConfigResponse)
async def get_config(engine=Depends(get_engine)):
    """Current server configuration (for the Settings page)."""
    # The engine creates its embedder lazily, so before the first store or
    # recall there is no instance to ask. Probe the resolution cheaply
    # instead of reporting the *requested* mode as if it were effective —
    # a page showing "auto" while the first embed will actually land on
    # hash is the same silence #519 is about.
    probed = probe_embedder_status(engine._embedder_mode)
    embedder = engine._embedder
    embedder_mode = embedder.mode if embedder else probed["effective_mode"]
    embedder_fallback_reason = (
        embedder.fallback_reason if embedder else probed["fallback_reason"]
    )
    recall_log_enabled, recall_log_days = engine._recall_log_config()
    return {
        "db_path": engine.db.db_path,
        "embedder_mode": embedder_mode,
        # The mode actually asked for (config/env) vs what's running, plus why
        # they differ when they do -- silently degrading to non-semantic hash
        # scoring gave no visible signal before this (#78).
        "requested_embedder_mode": embedder.requested_mode if embedder else probed["requested_mode"],
        "embedder_fallback_reason": embedder_fallback_reason,
        "embedder_dimension": engine._embedder.dimension if engine._embedder else None,
        "short_term_max": engine.short_term.max_size,
        "weights": {
            "alpha": engine.scorer.w.alpha,
            "beta": engine.scorer.w.beta,
            "gamma": engine.scorer.w.gamma,
            "delta": engine.scorer.w.delta,
        },
        "decay_half_life_hours": engine.scorer.half_life_hours,
        "reinforcement_gain": engine.scorer.reinforcement_gain,
        "max_stability_hours": engine.scorer.max_stability_hours,
        "auto_summarize_sessions": engine.auto_summarize,
        # Whether anything in this install may send memory content to a remote
        # model, so the Settings page can state it plainly instead of leaving
        # users to infer it from the presence of an API key.
        "outbound": llm_policy.outbound_status(),
        # Whether this install records what recall actually returned, and how
        # much it has recorded. Reported so the Settings page can state it: a
        # measurement nobody can see being on is a measurement nobody reads,
        # and a table quietly holding typed queries is worse than one the
        # operator knows about.
        "recall_log": {
            "enabled": recall_log_enabled,
            "retention_days": recall_log_days,
            **await engine.db.recall_log_stats(),
        },
        # Continuity proof (#378): how many briefs this install has handed
        # out, per channel, and how many surfaced memories a recent recall
        # actually came back. The pairing answers "are sessions starting
        # briefed, and does the brief matter" with producer-side numbers —
        # emission counts, never a delivery guarantee.
        "continuity_briefs": await engine.db.continuity_log.continuity_stats(),
        "version": APP_VERSION,
    }


def _liveness_report() -> dict:
    """The liveness report this process answers with, whoever asked.

    Unauthenticated (exempt from the token gate) so the dashboard can learn
    up-front whether it must ask the user for a token before any /api/* call.

    The override warning fires once per process and scrolls away, so the
    tokenless state it describes would vanish from every later observation.
    Reporting it here keeps the fact observable for as long as it holds
    (#151): `levh doctor` and any operator can ask the running server what
    boundary it actually enforces instead of trusting a startup log line.
    """
    return {
        "status": "ok",
        "service": "levh",
        "auth_required": bool(api_token()),
        "unauthenticated_remote_access": unauthenticated_remote_access_enabled(api_token()),
        # The bind address the process is configured to serve on, so an operator
        # (or `levh doctor`) can check the boundary the running server actually
        # has instead of inferring it. `cmd_serve` publishes its resolved
        # `--host` here, so argv and this field agree (issue #156).
        "api_host": configured_bind_host(),
    }


@router.get("/api/health", response_model=HealthResponse)
async def health():
    """Liveness: this process is up and answering (issue #520).

    Lives beside ``/api/readyz`` on purpose — readiness pings SQLite and
    reports the embedder actually running, liveness answers from the
    process's own state. The two are not interchangeable and are never
    collapsed into one.
    """
    return _liveness_report()


@router.get("/health", response_model=HealthResponse, include_in_schema=False)
async def health_alias():
    """The conventional liveness path, aliased onto ``/api/health``.

    A monitor that polls ``/health`` — the path uptime tooling and container
    health checks reach for by default — used to get a 404 on every single
    request, and a health checker that always sees the server down is worse
    than no checker: it triggers restarts and recovery churn against a
    perfectly healthy process (issue #520).

    The alias is hidden from the schema on purpose. ``/api/health`` is the
    published, unversioned liveness contract (see
    ``server/api_versioning.UNVERSIONED_PATHS``); this is a compatibility
    spelling, not a second documented surface, and publishing it would put
    two liveness paths in the frozen contract for no gain.

    It stays unauthenticated and out of ``/api/*``, so the token gate and the
    remote-access boundary keep treating it exactly like the probe it is: a
    report on this process, never a path to memory content.
    """
    return _liveness_report()


@router.get("/api/metrics", response_class=PlainTextResponse)
async def get_metrics():
    """Prometheus text exposition of this process's counters (issue #145).

    Scrapeable at ``/api/metrics`` and its versioned alias ``/api/v1/metrics``.
    The engine, the routes and the counters share one process, so the registry
    is process-wide and this handler is a read with no engine dependency —
    answering must not itself touch the database it reports on.
    """
    return PlainTextResponse(
        metrics.render(), media_type="text/plain; version=0.0.4; charset=utf-8"
    )


@router.get("/api/readyz", response_model=ReadyzResponse)
async def ready(engine=Depends(get_engine)):
    """Readiness probe: this process can actually serve reads and writes.

    ``/api/health`` is liveness — it answers from the process's own state and
    stays up even when the database is locked or the embedder is broken. That
    made the Docker ``HEALTHCHECK`` a lie: a wedged server still reported
    healthy (issue #145). This endpoint does the work liveness must not: it
    pings SQLite, reports the embedder mode actually running and whether a
    derived-state rebuild is behind.

    Failure is a 503 with the reasons, so an orchestrator that reads the status
    code (not the body) still routes around a broken instance. The body is the
    same shape either way.
    """
    reasons: list[str] = []

    try:
        cursor = await engine.db.conn.execute("SELECT 1")
        row = await cursor.fetchone()
        await cursor.close()
        db_ok = bool(row and row[0] == 1)
    except Exception as exc:  # noqa: BLE001 - a probe reports, it does not raise
        db_ok = False
        reasons.append(f"database: {type(exc).__name__}")

    if not db_ok:
        reasons.append("database ping failed")

    embedder = engine._embedder
    # The embedder is created lazily on first use, so a probe that arrives
    # before any store/recall has nothing to inspect. Reporting the
    # requested mode as the effective one there made a server whose first
    # embed would silently land on hash answer ``ready`` — exactly the
    # false readiness #519 describes. Resolve the same question without
    # loading a model: if the resolution *would* fall back, readiness says
    # so now, not after the degradation has already changed ranking.
    probed = probe_embedder_status(engine._embedder_mode)
    embedder_mode = embedder.mode if embedder else probed["effective_mode"]
    # A *requested* semantic embedder that fell back to hash is degraded, not
    # ready: recall quality changed silently (#78), and a load balancer should
    # not keep sending traffic to it as if nothing happened.
    embedder_ready = True
    if embedder is not None and embedder.fallback_reason:
        embedder_ready = False
        reasons.append(f"embedder: {embedder.fallback_reason}")
    elif embedder is None and probed["fallback_reason"]:
        embedder_ready = False
        reasons.append(f"embedder: {probed['fallback_reason']}")

    derived = None
    derived_stale = False
    try:
        derived = await engine.db.runtime_status()
        derived_stale = bool(derived.get("schema_version") != derived.get("schema_current"))
    except Exception as exc:  # noqa: BLE001 - probe reports, it does not raise
        reasons.append(f"runtime_status: {type(exc).__name__}")
    if derived_stale:
        reasons.append("derived-state schema is behind")

    ready_state = db_ok and embedder_ready and not derived_stale
    payload = {
        "status": "ready" if ready_state else "not_ready",
        "service": "levh",
        "database": "ok" if db_ok else "unavailable",
        "embedder_mode": embedder_mode,
        "embedder_ready": embedder_ready,
        "derived_state_current": not derived_stale,
        "reasons": reasons,
    }
    if not ready_state:
        return JSONResponse(status_code=503, content=payload)
    return payload


@router.post("/api/benchmark/recall", response_model=BenchmarkResponse)
async def benchmark_recall(embedder_mode: str = "", top_k: int = 5, engine=Depends(get_engine)):
    """Run the recall-quality benchmark harness (hit@k / MRR on a labelled
    corpus) and return the metrics — powers the Settings 'Recall Quality'
    panel. Runs against an isolated temp DB/engine, never touches real data.
    """
    from server.core.benchmark import run_benchmark

    mode = embedder_mode.strip() or engine.embedder.mode
    try:
        metrics = await run_benchmark(embedder_mode=mode, top_k=min(max(top_k, 1), 10))
    except Exception as e:  # noqa: BLE001 - a failed benchmark is a 500, not a crash
        logger.exception("recall benchmark failed")
        raise HTTPException(status_code=500, detail=f"Benchmark failed: {e}")
    return metrics
