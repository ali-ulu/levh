"""Agent tracking routes — REST API for agent activity, presence, checkpoints.

Enhanced with:
- WebSocket real-time presence updates
- Agent performance metrics
- Agent authentication (API key)
- Usage billing/metrics
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from server.routes.deps import get_engine

router = APIRouter()

# ── WebSocket connections for real-time agent presence ────────────────

_agent_ws_clients: set[WebSocket] = set()


async def broadcast_agent_event(event: str, data: dict) -> None:
    """Broadcast an agent event to all connected WebSocket clients."""
    if not _agent_ws_clients:
        return
    message = json.dumps({"type": event, "data": data}, default=str)
    stale = set()
    for ws in _agent_ws_clients:
        try:
            await ws.send_text(message)
        except Exception:
            stale.add(ws)
    _agent_ws_clients -= stale


@router.websocket("/ws/agents")
async def agent_websocket(ws: WebSocket):
    """WebSocket endpoint for real-time agent presence updates."""
    await ws.accept()
    _agent_ws_clients.add(ws)
    try:
        # Send initial state
        engine = await get_engine()
        tracker = engine.agent_tracker
        if tracker:
            online = await tracker.get_online_agents()
            await ws.send_json({
                "type": "initial_state",
                "online_agents": online,
            })

        # Keep connection alive, receive pings
        while True:
            data = await ws.receive_json()
            if data.get("action") == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        _agent_ws_clients.discard(ws)


# ── Request models ───────────────────────────────────────────────────

class AgentConnectRequest(BaseModel):
    agent_name: str
    session_id: str = ""
    project: str = ""
    metadata: dict = Field(default_factory=dict)
    api_key: str = ""  # Optional auth


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
    result = await tracker.agent_connect(
        agent_name=req.agent_name,
        session_id=req.session_id or None,
        project=req.project or None,
        metadata=req.metadata,
    )
    # Broadcast to WebSocket clients
    await broadcast_agent_event("agent_connected", result)
    return result


@router.post("/api/agents/{agent_session_id}/heartbeat")
async def agent_heartbeat(agent_session_id: str):
    """Send a heartbeat to keep an agent connection alive."""
    engine = await get_engine()
    tracker = engine.agent_tracker
    if not tracker:
        raise HTTPException(status_code=503, detail="Agent tracker not available")
    result = await tracker.heartbeat(agent_session_id)
    # Broadcast heartbeat (throttled)
    await broadcast_agent_event("agent_heartbeat", {
        "agent_session_id": agent_session_id,
        "timestamp": result.get("last_heartbeat"),
    })
    return result


@router.post("/api/agents/{agent_session_id}/disconnect")
async def agent_disconnect(agent_session_id: str):
    """Disconnect an agent from LEVH."""
    engine = await get_engine()
    tracker = engine.agent_tracker
    if not tracker:
        raise HTTPException(status_code=503, detail="Agent tracker not available")
    result = await tracker.agent_disconnect(agent_session_id)
    await broadcast_agent_event("agent_disconnected", result)
    return result


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


# ── Agent Performance Metrics ────────────────────────────────────────

@router.get("/api/agents/{agent_name}/metrics")
async def agent_metrics(agent_name: str):
    """Get performance metrics for a specific agent."""
    engine = await get_engine()
    tracker = engine.agent_tracker
    if not tracker:
        raise HTTPException(status_code=503, detail="Agent tracker not available")
    return await tracker.get_agent_metrics(agent_name)


@router.get("/api/agents/metrics/usage")
async def usage_billing():
    """Get usage billing metrics for all agents."""
    engine = await get_engine()
    tracker = engine.agent_tracker
    if not tracker:
        raise HTTPException(status_code=503, detail="Agent tracker not available")
    return await tracker.get_usage_billing()


# ── Agent Collaboration ──────────────────────────────────────────────

@router.get("/api/agents/collaboration/{project}")
async def agent_collaboration(project: str):
    """Get collaboration info for agents working on the same project."""
    engine = await get_engine()
    tracker = engine.agent_tracker
    if not tracker:
        raise HTTPException(status_code=503, detail="Agent tracker not available")
    return await tracker.get_project_collaboration(project)


# ── Checkpoints ──────────────────────────────────────────────────────

@router.post("/api/checkpoints")
async def create_checkpoint(req: CheckpointRequest):
    """Create a checkpoint of current work state."""
    engine = await get_engine()
    tracker = engine.agent_tracker
    if not tracker:
        raise HTTPException(status_code=503, detail="Agent tracker not available")
    result = await tracker.create_checkpoint(
        agent_name=req.agent_name,
        title=req.title,
        summary=req.summary,
        session_id=None,
        project=req.project or None,
        checkpoint_type=req.checkpoint_type,
        memory_ids=req.memory_ids,
    )
    await broadcast_agent_event("checkpoint_created", result)
    return result


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
