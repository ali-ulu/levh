"""Single-memory routes — everything under /api/memories/{memory_id}."""

from __future__ import annotations


from fastapi import APIRouter, HTTPException

from server.routes.deps import get_engine
from server.routes.models import FeedbackRequest, PinRequest, ReviewRequest, UpdateRequest

router = APIRouter()


@router.post("/api/memories/{memory_id}/review")
async def review_memory(memory_id: str, req: ReviewRequest):
    """Apply a spaced-repetition review decision to a memory."""
    engine = await get_engine()
    try:
        result = await engine.apply_review(
            memory_id, req.action, snooze_days=req.snooze_days, reason=req.reason
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("error", "memory not found"))
    return result


@router.get("/api/memories/{memory_id}")
async def get_memory(memory_id: str):
    engine = await get_engine()
    mem = await engine.get_memory(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="memory not found")
    return {**mem.model_dump(), "attachments": await engine.list_memory_attachments(memory_id)}


@router.put("/api/memories/{memory_id}")
async def update_memory(memory_id: str, req: UpdateRequest):
    engine = await get_engine()
    mem = await engine.update_memory(
        memory_id=memory_id,
        content=req.content,
        importance=req.importance,
        tags=req.tags,
        project=req.project,
        pinned=req.pinned,
    )
    if not mem:
        raise HTTPException(status_code=404, detail="memory not found")
    return mem.model_dump()


@router.patch("/api/memories/{memory_id}/pin")
async def pin_memory(memory_id: str, req: PinRequest):
    engine = await get_engine()
    mem = await engine.set_pinned(memory_id, req.pinned)
    if not mem:
        raise HTTPException(status_code=404, detail="memory not found")
    return mem.model_dump(exclude={"embedding"})


@router.post("/api/memories/{memory_id}/reinforce")
async def reinforce_memory(memory_id: str):
    """Manually strengthen a memory — resets its decay clock and grows its
    stability, the same reinforcement that happens automatically on recall."""
    engine = await get_engine()
    mem = await engine.reinforce_memory(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="memory not found")
    return mem.model_dump(exclude={"embedding"})


@router.post("/api/memories/{memory_id}/feedback")
async def memory_feedback(memory_id: str, req: FeedbackRequest):
    """Learn from recall outcomes: helpful=true reinforces the memory,
    helpful=false weakens it so wrong/stale information fades out fast."""
    engine = await get_engine()
    mem = await engine.memory_feedback(memory_id, req.helpful)
    if not mem:
        raise HTTPException(status_code=404, detail="memory not found")
    return mem.model_dump(exclude={"embedding"})


@router.post("/api/memories/{memory_id}/redact")
async def redact_memory(memory_id: str):
    """Strip secrets from an already-stored memory in place, recorded
    auditably in its metadata's redaction_history."""
    engine = await get_engine()
    result = await engine.redact_memory(memory_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("error", "memory not found"))
    return result


@router.post("/api/memories/{memory_id}/purge")
async def purge_memory(memory_id: str):
    """Hard-delete a memory across every layer and verify nothing survives.
    Pinned memories are purged too — this is a deliberate human action."""
    engine = await get_engine()
    return await engine.purge_memory(memory_id)


@router.get("/api/memories/{memory_id}/forgetting-curve")
async def get_forgetting_curve(memory_id: str, days: int = 30):
    """Predicted retention curve for a memory — powers the 'memory strength'
    visualization in the dashboard's detail drawer."""
    engine = await get_engine()
    curve = await engine.get_forgetting_curve(memory_id, days=min(max(days, 1), 365))
    if not curve:
        raise HTTPException(status_code=404, detail="memory not found")
    return curve


@router.get("/api/memories/{memory_id}/trust")
async def get_memory_trust(memory_id: str):
    """Provenance/trust breakdown for a memory — explainable, deterministic,
    NOT truth, and independent of H-score recall ranking."""
    engine = await get_engine()
    result = await engine.get_trust(memory_id)
    if result is None:
        raise HTTPException(status_code=404, detail="memory not found")
    return result


@router.get("/api/memories/{memory_id}/related")
async def related_memories(memory_id: str, top_k: int = 5):
    """Memories most similar to this one — the 'related memories' graph edge,
    computed live from embeddings. Powers 'see also' in the detail drawer."""
    engine = await get_engine()
    if not await engine.get_memory(memory_id):
        raise HTTPException(status_code=404, detail="memory not found")
    related = await engine.get_related(memory_id, top_k=min(max(top_k, 1), 20))
    return {
        "memory_id": memory_id,
        "related": [
            {**m.model_dump(exclude={"embedding"}), "similarity": round(sim, 4)}
            for m, sim in related
        ],
    }


@router.delete("/api/memories/{memory_id}")
async def delete_memory(memory_id: str):
    engine = await get_engine()
    success = await engine.forget(memory_id)
    return {"deleted": success}


@router.get("/api/memories/{memory_id}/score-breakdown")
async def get_score_breakdown(memory_id: str, query: str = ""):
    """Return H(x,ψ) score breakdown for a specific memory + query pair.

    If query is empty, memory.content is used as the default query so the
    breakdown reflects self-similarity (baseline score for the memory).
    """
    engine = await get_engine()
    mem = await engine.get_memory(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="memory not found")

    # Guard: empty query falls back to memory content (produces baseline score)
    effective_query = query.strip() if query.strip() else mem.content

    # Compute similarity between query and memory
    query_embedding = await engine.embedder.embed(effective_query)
    memory_embedding = mem.embedding
    if memory_embedding is None:
        raise HTTPException(status_code=400, detail="memory has no embedding")

    # Manual cosine similarity
    import numpy as np
    q = np.asarray(query_embedding, dtype=np.float64)
    m = np.asarray(memory_embedding, dtype=np.float64)
    if q.shape != m.shape:
        raise HTTPException(
            status_code=400,
            detail="embedding dimension mismatch (embedder mode changed since storage)",
        )
    norm_q = np.linalg.norm(q)
    norm_m = np.linalg.norm(m)
    similarity = float(np.dot(q, m) / max(norm_q * norm_m, 1e-9))
    similarity = max(0.0, min(1.0, similarity))

    decay = (
        1.0
        if mem.pinned
        else engine.scorer.compute_decay(mem.accessed_at, half_life_hours=mem.stability_hours)
    )
    bd = engine.scorer.breakdown(
        similarity=similarity,
        decay_factor=decay,
        importance=mem.importance,
        frequency=mem.frequency,
    )
    score = engine.scorer.compute(
        similarity=similarity,
        decay_factor=decay,
        importance=mem.importance,
        frequency=mem.frequency,
    )

    return {
        "score": score,
        "components": {
            "similarity_penalty": bd["alpha_component"],
            "decay_penalty": bd["beta_component"],
            "importance_penalty": bd["gamma_component"],
            "frequency_penalty": bd["delta_component"],
        },
        "weights": {
            "alpha": engine.scorer.w.alpha,
            "beta": engine.scorer.w.beta,
            "gamma": engine.scorer.w.gamma,
            "delta": engine.scorer.w.delta,
        },
    }
