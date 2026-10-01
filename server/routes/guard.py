"""Mistake guard routes."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from server.core.guard import GuardService
    from server.core.memory_engine import MemoryEngine


from fastapi import APIRouter, Depends, HTTPException

from server.routes.deps import get_engine
from server.routes.models import (
    CheckActionRequest,
    GuardCheckResponse,
    GuardMistakeResponse,
    GuardRuleListResponse,
    GuardViolationListResponse,
    MistakeRequest,
)

router = APIRouter()


def _get_guard(engine: "MemoryEngine") -> "GuardService":
    from server.core.guard import GuardService

    return GuardService(engine.db, engine)


@router.get("/api/guard/violations", response_model=GuardViolationListResponse)
async def list_guard_violations(
    days: int = 0, severity: str = "", limit: int = 50,
    engine: "MemoryEngine" = Depends(get_engine),
):
    """List recorded mistakes, newest first. ``days=0`` means all time."""
    guard = _get_guard(engine)
    return {
        "violations": await guard.list_violations(
            days=days or None, severity=severity or None, limit=limit
        )
    }


@router.get("/api/guard/rules", response_model=GuardRuleListResponse)
async def list_guard_rules(
    project: str = "", limit: int = 50,
    engine: "MemoryEngine" = Depends(get_engine),
):
    """List the pinned rules mistakes have produced, most important first."""
    guard = _get_guard(engine)
    rules = await guard.list_rules(project=project or None, limit=limit)
    return {
        "rules": [
            {
                "id": r.id,
                "statement": r.content,
                "importance": r.importance,
                "severity": r.metadata.get("severity", "medium"),
                "task": r.metadata.get("task", ""),
                "correct_action": r.metadata.get("correct_action", ""),
                "root_cause": r.metadata.get("root_cause", ""),
                "project": r.project,
                "created_at": r.created_at,
            }
            for r in rules
        ]
    }


@router.post("/api/guard/check", response_model=GuardCheckResponse)
async def check_guard_action(
    req: CheckActionRequest,
    engine: "MemoryEngine" = Depends(get_engine),
):
    """Judge a proposed action against the recorded rules, before it runs.

    Read-only and advisory: returns ``warn`` when a recorded rule overlaps the
    action and ``allow`` otherwise. It never returns ``block`` — the caller
    decides whether a warning is an instruction.
    """
    guard = _get_guard(engine)
    return await guard.check_action(
        tool_name=req.tool_name,
        action_text=req.action_text,
        project=req.project or None,
    )


@router.post("/api/guard/mistakes", response_model=GuardMistakeResponse)
async def record_guard_mistake(
    req: MistakeRequest,
    engine: "MemoryEngine" = Depends(get_engine),
):
    """Record a mistake as a pinned rule plus a violation row."""
    guard = _get_guard(engine)
    try:
        return await guard.record_mistake(
            task=req.task,
            wrong_action=req.wrong_action,
            correct_action=req.correct_action,
            root_cause=req.root_cause,
            tool_name=req.tool_name,
            severity=req.severity,
            source=req.source,
            project=req.project,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
