"""Memory collection routes — list, store, search, and bulk operations."""

from __future__ import annotations


from fastapi import APIRouter, HTTPException

from server.core.types import RecallRequest
from server.routes.deps import get_engine
from server.routes.models import AdmissionEvalRequest, AdmitRequest, AskRequest, ConsolidateRequest, DedupeRequest, ImportRequest, RedactAllRequest, StoreRequest
from server.routes.deps import public_demo

router = APIRouter()


@router.post("/api/memories")
async def store_memory(req: StoreRequest):
    """Default product write path: admission gate before persistence.

    ``force=true`` is an explicit audited override for administrative recovery;
    the admission decision is still recorded in memory metadata.
    """
    engine = await get_engine()
    try:
        result = await engine.admit_memory(
            content=req.content,
            importance=req.importance,
            tags=req.tags,
            session_id=req.session_id,
            project=req.project,
            source=req.source or "dashboard",
            pinned=req.pinned,
            memory_type=req.memory_type,
            metadata=req.metadata,
            force=req.force,
            min_length=req.min_length,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if not result["stored"]:
        raise HTTPException(
            status_code=409,
            detail={"message": "memory not stored by admission gate", "decision": result["decision"]},
        )
    return result["memory"]


@router.get("/api/memories")
async def list_memories(
    memory_type: str = "",
    session_id: str = "",
    project: str = "",
    source: str = "",
    tag: str = "",
    pinned: str = "",
    q: str = "",
    min_importance: float = 0.0,
    limit: int = 50,
    offset: int = 0,
):
    engine = await get_engine()
    memories = await engine.list_memories(
        memory_type=memory_type or None,
        session_id=session_id or None,
        project=project or None,
        source=source or None,
        tag=tag or None,
        pinned={"true": True, "false": False}.get(pinned.lower(), None),
        min_importance=min_importance if min_importance > 0 else None,
        content_like=q or None,
        limit=limit,
        offset=offset,
    )
    attachments_by_memory = await engine.db.list_attachments_for_memories([m.id for m in memories])
    return [
        {**m.model_dump(exclude={"embedding"}), "attachments": attachments_by_memory.get(m.id, [])}
        for m in memories
    ]


@router.get("/api/memories/fading")
async def list_fading_memories(threshold: float = 0.35, project: str = "", limit: int = 20):
    """Memories predicted to be nearly forgotten — the review queue."""
    engine = await get_engine()
    fading = await engine.list_fading(
        threshold=max(0.01, min(0.99, threshold)),
        project=project or None,
        limit=min(max(limit, 1), 100),
    )
    return [
        {**m.model_dump(exclude={"embedding"}), "retention": retention}
        for m, retention in fading
    ]


@router.get("/api/memories/review")
async def get_review_queue(threshold: float = 0.5, project: str = "", limit: int = 50):
    """Spaced-repetition review queue — fading, unpinned, un-snoozed memories
    due for a keep/reinforce/weaken/pin/forget/snooze decision."""
    engine = await get_engine()
    return {
        "review": await engine.review_queue(
            threshold=max(0.01, min(0.99, threshold)),
            project=project or None,
            limit=min(max(limit, 1), 200),
        )
    }


@router.get("/api/memories/held")
async def list_held_memories(status: str = "held", project: str = "", limit: int = 50):
    """Candidates the admission gate answered ``review`` for and parked for a
    human — near-duplicates it declined to decide on its own.

    Distinct from ``/api/memories/review``, which is the spaced-repetition queue
    over memories that were already admitted. Nothing listed here is a memory
    yet. ``status=""`` returns every decision state, which is how the queue
    doubles as a record of what was decided."""
    engine = await get_engine()
    return {
        "held": await engine.db.list_held_memories(
            status=status, project=project or None, limit=limit
        ),
        "waiting": await engine.db.count_held_memories(),
    }


@router.post("/api/memories/held/{held_id}/admit")
async def admit_held_memory(held_id: str):
    """Keep a held candidate: store it as the memory it was going to be, with
    the importance, tags, session, project, source and type it arrived with."""
    engine = await get_engine()
    result = await engine.admit_held_memory(held_id)
    if not result["ok"]:
        raise HTTPException(
            status_code=404 if result["error"] == "not_found" else 409,
            detail=result["error"],
        )
    return result


@router.post("/api/memories/held/{held_id}/discard")
async def discard_held_memory(held_id: str):
    """Drop a held candidate. The row stays with its verdict, so a discard is
    recorded rather than leaving no trace."""
    engine = await get_engine()
    result = await engine.discard_held_memory(held_id)
    if not result["ok"]:
        raise HTTPException(
            status_code=404 if result["error"] == "not_found" else 409,
            detail=result["error"],
        )
    return result


@router.get("/api/memories/audit-secrets")
async def get_audit_secrets():
    """Read-only scan for secrets (credentials, tokens) that slipped into
    stored memories before the admission gate existed."""
    engine = await get_engine()
    return {"audit": await engine.audit_secrets()}


@router.post("/api/memories/redact-all")
async def redact_all_secrets(req: RedactAllRequest):
    """Bulk redaction of secrets across stored memories. dry_run=true
    (default) only previews; set false to rewrite every flagged memory."""
    engine = await get_engine()
    return await engine.redact_all_secrets(dry_run=req.dry_run)


@router.get("/api/memories/low-trust")
async def get_low_trust_memories(threshold: float = 0.4, limit: int = 50):
    """Stored memories whose provenance/trust confidence is below
    ``threshold`` (least confident first). Run trust/recompute first to
    populate. Provenance is NOT truth — it does not change H-score ranking."""
    engine = await get_engine()
    return {"low_trust": await engine.list_low_trust(threshold=threshold, limit=limit)}


@router.post("/api/memories/trust/recompute")
async def recompute_trust_scores():
    """Compute and persist the provenance/trust score for every memory."""
    engine = await get_engine()
    return await engine.recompute_trust_scores()


@router.post("/api/memories/recall")
async def recall_memories(req: RecallRequest):
    engine = await get_engine()
    result = await engine.recall(
        query=req.query,
        top_k=req.top_k,
        session_id=req.session_id,
        project=req.project,
        min_importance=req.min_importance,
        # Reinforcement resets decay clocks and raises frequency. On a public
        # demo the store is shared, so an anonymous search must not reshape
        # what everyone else sees.
        reinforce=False if public_demo() else req.reinforce,
    )
    attachments_by_memory = await engine.db.list_attachments_for_memories(
        [m.id for m in result.memories]
    )
    return {
        "memories": [
            {**m.model_dump(exclude={"embedding"}), "attachments": attachments_by_memory.get(m.id, [])}
            for m in result.memories
        ],
        "scores": result.scores,
    }


@router.post("/api/ask")
async def ask_memory(req: AskRequest):
    """Ask your memory a question and get a synthesized, cited answer.

    Grounded only in stored memories; each source is returned so the UI can
    show citations. Read-only — asking does not reinforce memories. Uses an LLM
    when OPENAI_API_KEY is set, otherwise returns ranked evidence (offline)."""
    engine = await get_engine()
    return await engine.ask(
        question=req.question,
        top_k=min(max(req.top_k, 1), 20),
        session_id=req.session_id,
        project=req.project,
        min_importance=req.min_importance,
    )


@router.post("/api/memories/consolidate")
async def consolidate_memories(session_id: str = ""):
    engine = await get_engine()
    count = await engine.consolidate(session_id=session_id or None)
    return {"consolidated": count}


@router.post("/api/memories/dedupe")
async def dedupe_memories(req: DedupeRequest):
    """Find (dry_run) or remove near-duplicate memories."""
    engine = await get_engine()
    if req.dry_run:
        groups = await engine.find_duplicates(
            similarity_threshold=req.similarity_threshold, project=req.project
        )
        return {
            "dry_run": True,
            "groups": [
                [m.model_dump(exclude={"embedding"}) for m in group]
                for group in groups
            ],
            "duplicates": sum(len(g) - 1 for g in groups),
        }
    removed = await engine.dedupe(
        similarity_threshold=req.similarity_threshold, project=req.project
    )
    return {"dry_run": False, "removed": removed}


@router.post("/api/memories/consolidate-similar")
async def consolidate_similar_memories(req: ConsolidateRequest):
    """Preview (dry_run) or apply sleep-like consolidation: cluster related
    older memories and compress each cluster into one consolidated memory,
    archiving the originals inside it."""
    engine = await get_engine()
    return await engine.consolidate_memories(
        similarity_threshold=req.similarity_threshold,
        min_age_days=req.min_age_days,
        min_cluster_size=req.min_cluster_size,
        project=req.project,
        dry_run=req.dry_run,
    )


@router.post("/api/memories/evaluate-admission")
async def evaluate_admission(req: AdmissionEvalRequest):
    """Preview the admission gate's verdict for a candidate memory WITHOUT
    storing it: admit / review / redact / reject."""
    engine = await get_engine()
    return {
        "decision": await engine.evaluate_admission(
            req.content, project=req.project or None, min_length=req.min_length
        )
    }


@router.post("/api/memories/admit")
async def admit_memory(req: AdmitRequest):
    """Store a candidate memory through the admission gate: dedupe + secret
    redaction. reject/review are not stored unless force=True."""
    engine = await get_engine()
    try:
        return await engine.admit_memory(
            content=req.content,
            importance=req.importance,
            tags=req.tags,
            session_id=req.session_id,
            project=req.project,
            source=req.source or "dashboard",
            pinned=req.pinned,
            memory_type=req.memory_type,
            metadata=req.metadata,
            force=req.force,
            min_length=req.min_length,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/api/memories/export")
async def export_memories(session_id: str = ""):
    engine = await get_engine()
    data = await engine.export_memories(session_id=session_id or None)
    return {"count": len(data), "data": data}


@router.post("/api/memories/import")
async def import_memories(req: ImportRequest):
    engine = await get_engine()
    return await engine.import_memories_gated(req.data)
