"""Conflict-candidate routes."""

from __future__ import annotations


from fastapi import Depends, APIRouter, HTTPException

from server.routes.deps import get_engine
from server.routes.models import ConflictReviewRequest

router = APIRouter()


@router.post("/api/conflicts/detect")
async def detect_conflicts(engine=Depends(get_engine)):
    """Scan stored memories for conflict CANDIDATES — pairs that share an
    entity and show an opposing surface pattern. Idempotent: never resets
    already-reviewed candidates."""
    return await engine.detect_conflict_candidates()


@router.get("/api/conflicts")
async def list_conflicts(status: str = "open", limit: int = 100, engine=Depends(get_engine)):
    """List conflict candidates, optionally filtered by status. Pass an empty
    status to list every status."""
    return {"conflicts": await engine.list_conflict_candidates(status=status or None, limit=limit)}


@router.post("/api/conflicts/{conflict_id:path}/review")
async def review_conflict(conflict_id: str, req: ConflictReviewRequest, engine=Depends(get_engine)):
    """Apply a human review decision to a conflict candidate. ``conflict_id``
    may contain ``|`` so the path converter is used."""
    try:
        result = await engine.review_conflict_candidate(conflict_id, req.action)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("error", "conflict not found"))
    return result
