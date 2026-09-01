"""System routes — stats, config, health, benchmark."""

from __future__ import annotations


from fastapi import APIRouter, HTTPException

from server.core import llm_policy
from server.routes.deps import get_engine
from server.routes.deps import APP_VERSION, api_token, logger

router = APIRouter()


@router.get("/api/stats")
async def get_stats():
    engine = await get_engine()
    stats = await engine.get_stats()
    return stats.model_dump()


@router.get("/api/config")
async def get_config():
    """Current server configuration (for the Settings page)."""
    engine = await get_engine()
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
    return {
        "status": "ok",
        "service": "levh",
        "auth_required": bool(api_token()),
    }


@router.post("/api/benchmark/recall")
async def benchmark_recall(embedder_mode: str = "", top_k: int = 5):
    """Run the recall-quality benchmark harness (hit@k / MRR on a labelled
    corpus) and return the metrics — powers the Settings 'Recall Quality'
    panel. Runs against an isolated temp DB/engine, never touches real data.
    """
    from server.core.benchmark import run_benchmark

    engine = await get_engine()
    mode = embedder_mode.strip() or engine.embedder.mode
    try:
        metrics = await run_benchmark(embedder_mode=mode, top_k=min(max(top_k, 1), 10))
    except Exception as e:
        logger.exception("recall benchmark failed")
        raise HTTPException(status_code=500, detail=f"Benchmark failed: {e}")
    return metrics
