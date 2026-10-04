"""Team Memory collaboration routes (#377)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from server.routes.deps import get_engine
from server.routes.models import (
    ConflictReviewRequest,
    TeamDecisionConflictDetectResponse,
    TeamDecisionConflictOut,
    TeamDecisionConflictReviewResponse,
    TeamDecisionCreateRequest,
    TeamDecisionCreateResponse,
    TeamDecisionOut,
    TeamHandoffCreateRequest,
    TeamHandoffOut,
)

router = APIRouter()


def _tracker(engine):
    tracker = engine.agent_tracker
    if not tracker:
        raise HTTPException(status_code=503, detail="Agent tracker not available")
    return tracker


@router.post("/api/team/handoffs", response_model=TeamHandoffOut)
async def create_handoff(req: TeamHandoffCreateRequest, engine=Depends(get_engine)):
    """Create an explicit work/context handoff for another agent."""
    try:
        return await _tracker(engine).create_handoff(
            project=req.project,
            to_agent=req.to_agent,
            title=req.title,
            summary=req.summary,
            memory_ids=req.memory_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/team/handoffs", response_model=list[TeamHandoffOut])
async def list_handoffs(
    project: str = "",
    status: str = "",
    to_agent: str = "",
    limit: int = 100,
    engine=Depends(get_engine),
):
    """List handoffs visible to the current workspace."""
    return await _tracker(engine).list_handoffs(
        project=project or None,
        status=status or None,
        to_agent=to_agent or None,
        limit=limit,
    )


@router.post("/api/team/handoffs/{handoff_id}/accept", response_model=TeamHandoffOut)
async def accept_handoff(handoff_id: str, engine=Depends(get_engine)):
    """Accept a pending handoff."""
    try:
        return await _tracker(engine).accept_handoff(handoff_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="handoff not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/team/handoffs/{handoff_id}/complete", response_model=TeamHandoffOut)
async def complete_handoff(handoff_id: str, engine=Depends(get_engine)):
    """Mark an accepted handoff complete."""
    try:
        return await _tracker(engine).complete_handoff(handoff_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="handoff not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/team/decisions", response_model=TeamDecisionCreateResponse)
async def create_decision(req: TeamDecisionCreateRequest, engine=Depends(get_engine)):
    """Record a shared decision; conflicting active statements become contested."""
    try:
        return await _tracker(engine).create_team_decision(
            project=req.project,
            decision_key=req.decision_key,
            statement=req.statement,
            rationale=req.rationale,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/team/decisions", response_model=list[TeamDecisionOut])
async def list_decisions(
    project: str = "",
    decision_key: str = "",
    status: str = "",
    limit: int = 100,
    engine=Depends(get_engine),
):
    """List shared project decisions visible to the current workspace."""
    return await _tracker(engine).list_team_decisions(
        project=project or None,
        decision_key=decision_key or None,
        status=status or None,
        limit=limit,
    )


@router.post("/api/team/decisions/{decision_id}/resolve", response_model=TeamDecisionOut)
async def resolve_decision(decision_id: str, engine=Depends(get_engine)):
    """Choose one contested decision. Admin-only via storage authorization."""
    try:
        return await _tracker(engine).resolve_team_decision(decision_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="decision not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/api/team/decision-conflicts/detect",
    response_model=TeamDecisionConflictDetectResponse,
)
async def detect_decision_conflicts(
    project: str = "",
    engine=Depends(get_engine),
):
    """Detect review-worthy free-text conflicts between differently-keyed decisions."""
    return await _tracker(engine).detect_team_decision_conflicts(
        project=project or None
    )


@router.get(
    "/api/team/decision-conflicts",
    response_model=list[TeamDecisionConflictOut],
)
async def list_decision_conflicts(
    project: str = "",
    status: str = "open",
    limit: int = 100,
    engine=Depends(get_engine),
):
    """List semantic decision conflict candidates in the current workspace."""
    return await _tracker(engine).list_team_decision_conflicts(
        project=project or None,
        status=status or None,
        limit=limit,
    )


@router.post(
    "/api/team/decision-conflicts/{conflict_id:path}/review",
    response_model=TeamDecisionConflictReviewResponse,
)
async def review_decision_conflict(
    conflict_id: str,
    req: ConflictReviewRequest,
    engine=Depends(get_engine),
):
    """Apply an explicit admin review; the candidate is never an auto-verdict."""
    try:
        conflict = await _tracker(engine).review_team_decision_conflict(
            conflict_id,
            req.action,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="decision conflict not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "action": req.action, "conflict": conflict}
