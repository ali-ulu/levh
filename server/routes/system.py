"""System routes — stats, config, health, benchmark."""

from __future__ import annotations


from fastapi import Depends, APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

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
