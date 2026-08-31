"""Agent tracking routes — REST API for agent activity, presence, checkpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from server.routes.deps import get_engine

router = APIRouter()


# ── Request models ───────────────────────────────────────────────────

class AgentConnectRequest(BaseModel):
    agent_name: str
    session_id: str = ""
    project: str = ""
    metadata: dict = Field(default_factory=dict)


class CheckpointRequest(BaseModel):
    agent_name: str = "unknown"
    title: str = "Work checkpoint"
    summary: str = ""
    project: str = ""
    checkpoint_type: str = "manual"
    memory_ids: list[str] = Field(default_factory=list)


# ── Agent connection ─────────────────────────────────────────────────

@router.post("/api/agents/connect")
async def agent_connect(req: AgentConnectRequest):
    """Record an agent connecting to LEVH."""
    engine = await get_engine()
    tracker = engine.agent_tracker
    if not tracker:
        raise HTTPException(status_code=503, detail="Agent tracker not available")
    return await tracker.agent_connect(
        agent_name=req.agent_name,
        session_id=req.session_id or None,
        project=req.project or None,
        metadata=req.metadata,
    )


@router.post("/api/agents/{agent_session_id}/heartbeat")
async def agent_heartbeat(agent_session_id: str):
    """Send a heartbeat to keep an agent connection alive."""
    engine = await get_engine()
    tracker = engine.agent_tracker
    if not tracker:
        raise HTTPException(status_code=503, detail="Agent tracker not available")
    return await tracker.heartbeat(agent_session_id)


@router.post("/api/agents/{agent_session_id}/disconnect")
async def agent_disconnect(agent_session_id: str):
    """Disconnect an agent from LEVH."""
    engine = await get_engine()
    tracker = engine.agent_tracker
    if not tracker:
        raise HTTPException(status_code=503, detail="Agent tracker not available")
    return await tracker.agent_disconnect(agent_session_id)


# ── Agent queries ────────────────────────────────────────────────────

@router.get("/api/agents")
async def list_agents(limit: int = 50):
    """List all agent connections (active and disconnected)."""
    engine = await get_engine()
    tracker = engine.agent_tracker
    if not tracker:
        raise HTTPException(status_code=503, detail="Agent tracker not available")
    return await tracker.get_agent_activity(limit=limit)


@router.get("/api/agents/online")
async def list_online_agents():
    """List currently online agents."""
    engine = await get_engine()
    tracker = engine.agent_tracker
    if not tracker:
        raise HTTPException(status_code=503, detail="Agent tracker not available")
    return await tracker.get_online_agents()


@router.get("/api/agents/stats")
async def agent_stats():
    """Get aggregate agent usage statistics."""
    engine = await get_engine()
    tracker = engine.agent_tracker
    if not tracker:
        raise HTTPException(status_code=503, detail="Agent tracker not available")
    return await tracker.get_agent_stats()


# ── Checkpoints ──────────────────────────────────────────────────────

@router.post("/api/checkpoints")
async def create_checkpoint(req: CheckpointRequest):
    """Create a checkpoint of current work state."""
    engine = await get_engine()
    tracker = engine.agent_tracker
    if not tracker:
        raise HTTPException(status_code=503, detail="Agent tracker not available")
    return await tracker.create_checkpoint(
        agent_name=req.agent_name,
        title=req.title,
        summary=req.summary,
        session_id=None,
        project=req.project or None,
        checkpoint_type=req.checkpoint_type,
        memory_ids=req.memory_ids,
    )


@router.get("/api/checkpoints")
async def list_checkpoints(
    agent_name: str = "",
    project: str = "",
    limit: int = 50,
):
    """List recent checkpoints."""
    engine = await get_engine()
    tracker = engine.agent_tracker
    if not tracker:
        raise HTTPException(status_code=503, detail="Agent tracker not available")
    return await tracker.list_checkpoints(
        agent_name=agent_name or None,
        project=project or None,
        limit=limit,
    )
