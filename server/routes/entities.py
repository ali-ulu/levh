"""Entity knowledge-graph routes."""

from __future__ import annotations


from fastapi import Depends, APIRouter, HTTPException

from server.routes.deps import get_engine

router = APIRouter()


@router.post("/api/entities/reindex")
async def reindex_entities(engine=Depends(get_engine)):
    """Rebuild the persistent entity graph from every stored memory."""
    return await engine.reindex_entities()


@router.get("/api/entities/stats")
async def entity_graph_stats(engine=Depends(get_engine)):
    """Counts of persisted entities by type."""
    return await engine.entity_graph_stats()


@router.get("/api/entities")
async def list_entities_graph(type: str = "", limit: int = 200, engine=Depends(get_engine)):
    """Persisted entities (optionally filtered by type), most-mentioned first."""
    return {"entities": await engine.list_entities_graph(entity_type=type or None, limit=limit)}


@router.get("/api/entities/{entity_id:path}")
async def get_entity(entity_id: str, engine=Depends(get_engine)):
    """An entity's profile: the memories that mention it and the entities it
    co-occurs with. ``entity_id`` may be a full id like ``person:alice@acme.com``
    or a free-text query (resolved by best match)."""
    result = await engine.get_entity(entity_id)
    if result is None:
        raise HTTPException(status_code=404, detail="entity not found")
    return result
