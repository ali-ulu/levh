"""Session routes."""

from __future__ import annotations


from fastapi import APIRouter, HTTPException

from server.routes.deps import get_engine
from server.routes.models import CreateSessionRequest

router = APIRouter()


@router.post("/api/sessions")
async def create_session(req: CreateSessionRequest):
    engine = await get_engine()
    session = await engine.create_session(name=req.name, metadata=req.metadata)
    return session.model_dump()


@router.get("/api/sessions")
async def list_sessions(limit: int = 50):
    engine = await get_engine()
    sessions = await engine.list_sessions(limit=limit)
    return [s.model_dump() for s in sessions]


@router.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    engine = await get_engine()
    session = await engine.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    return session.model_dump()


@router.patch("/api/sessions/{session_id}/end")
async def end_session(session_id: str):
    engine = await get_engine()
    session = await engine.end_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    return session.model_dump()


@router.post("/api/sessions/{session_id}/summarize")
async def summarize_session(session_id: str):
    """Distill a session's memories into one durable summary memory (LLM when
    OPENAI_API_KEY is set, deterministic extractive fallback otherwise)."""
    engine = await get_engine()
    if not await engine.get_session(session_id):
        raise HTTPException(status_code=404, detail="session not found")
    summary = await engine.summarize_session(session_id)
    if not summary:
        return {"summarized": False, "reason": "no memories in session"}
    return {"summarized": True, "summary": summary.model_dump(exclude={"embedding"})}
