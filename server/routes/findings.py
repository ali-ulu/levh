"""Findings inbox routes — what LEVH noticed, waiting for a human.

Reporting is a write with no side effects beyond this table: a finding never
triggers a fix, a command, or an outbound request. The decision endpoint is
the only way a finding changes state, and a person is the only caller of it.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from server.core import findings as findings_core
from server.routes.deps import get_engine
from server.routes.models import FindingDecisionRequest, FindingReportRequest

router = APIRouter()


@router.get("/api/findings")
async def list_findings(status: str = "open", category: str = "", limit: int = 100):
    """List findings, newest sighting first. Pass an empty status for all
    states — that is the "what did we decide about these" view."""
    engine = await get_engine()
    return {
        "findings": await engine.db.list_findings(
            status=status, category=category or None, limit=limit
        ),
        "counts": await engine.db.count_findings_by_status(),
    }


@router.post("/api/findings")
async def report_finding(req: FindingReportRequest):
    """Record a finding. Scrubbed and fingerprinted before it is stored, so a
    repeat folds into the existing row instead of creating a new one."""
    engine = await get_engine()
    row = findings_core.build_row(
        title=req.title,
        detail=req.detail,
        category=req.category,
        severity=req.severity,
        source=req.source,
    )
    if not row["title"]:
        raise HTTPException(status_code=422, detail="title cannot be empty")
    return await engine.db.record_finding(row)


@router.post("/api/findings/{finding_id}/decide")
async def decide_finding(finding_id: str, req: FindingDecisionRequest):
    """Apply a human decision: ack, resolved or ignored (open reopens it)."""
    engine = await get_engine()
    try:
        result = await engine.db.decide_finding(finding_id, req.status, req.note or None)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if result is None:
        raise HTTPException(status_code=404, detail="finding not found")
    return result


@router.delete("/api/findings/{finding_id}")
async def delete_finding(finding_id: str):
    engine = await get_engine()
    if not await engine.db.delete_finding(finding_id):
        raise HTTPException(status_code=404, detail="finding not found")
    return {"ok": True, "deleted": finding_id}
