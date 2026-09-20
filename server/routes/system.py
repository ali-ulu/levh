"""System routes — stats, config, health, benchmark."""

from __future__ import annotations


from fastapi import Depends, APIRouter, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse

from server.core import llm_policy, metrics
from server.auth import unauthenticated_remote_access_enabled
from server.core.runtime_config import configured_bind_host
from server.routes.deps import get_engine
from server.routes.deps import APP_VERSION, api_token, logger

router = APIRouter()


@router.get("/api/stats")
async def get_stats(engine=Depends(get_engine)):
    stats = await engine.get_stats()
    return stats.model_dump()


@router.get("/api/config")
async def get_config(engine=Depends(get_engine)):
    """Current server configuration (for the Settings page)."""
    embedder_mode = engine._embedder.mode if engine._embedder else engine._embedder_mode
    return {
        "db_path": engine.db.db_path,
        "embedder_mode": embedder_mode,
        # The mode actually asked for (config/env) vs what's running, plus why
        # they differ when they do -- silently degrading to non-semantic hash
        # scoring gave no visible signal before this (#78).
        "requested_embedder_mode": engine._embedder.requested_mode if engine._embedder else engine._embedder_mode,
        "embedder_fallback_reason": engine._embedder.fallback_reason if engine._embedder else None,
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
        "version": APP_VERSION,
    }


@router.get("/api/health")
async def health():
    # Unauthenticated (exempt from the token gate) so the dashboard can learn
    # up-front whether it must ask the user for a token before any /api/* call.
    #
    # The override warning fires once per process and scrolls away, so the
    # tokenless state it describes would vanish from every later observation.
    # Reporting it here keeps the fact observable for as long as it holds
    # (#151): `levh doctor` and any operator can ask the running server what
    # boundary it actually enforces instead of trusting a startup log line.
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


@router.get("/api/readyz")
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
    embedder_mode = embedder.mode if embedder else engine._embedder_mode
    # A *requested* semantic embedder that fell back to hash is degraded, not
    # ready: recall quality changed silently (#78), and a load balancer should
    # not keep sending traffic to it as if nothing happened.
    embedder_ready = True
    if embedder is not None and embedder.fallback_reason:
        embedder_ready = False
        reasons.append(f"embedder: {embedder.fallback_reason}")

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


@router.post("/api/benchmark/recall")
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
