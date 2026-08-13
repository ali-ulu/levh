"""Mistake guard routes."""

from __future__ import annotations


from fastapi import APIRouter, HTTPException

from server.routes.deps import get_engine
from server.routes.models import MistakeRequest

router = APIRouter()


async def _get_guard():
    from server.core.guard import GuardService

    engine = await get_engine()
    return GuardService(engine.db, engine)


@router.get("/api/guard/violations")
async def list_guard_violations(days: int = 0, severity: str = "", limit: int = 50):
    """List recorded mistakes, newest first. ``days=0`` means all time."""
    guard = await _get_guard()
    return {
        "violations": await guard.list_violations(
            days=days or None, severity=severity or None, limit=limit
        )
    }


@router.get("/api/guard/rules")
async def list_guard_rules(project: str = "", limit: int = 50):
    """List the pinned rules mistakes have produced, most important first."""
    guard = await _get_guard()
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


@router.post("/api/guard/mistakes")
async def record_guard_mistake(req: MistakeRequest):
    """Record a mistake as a pinned rule plus a violation row."""
    guard = await _get_guard()
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
