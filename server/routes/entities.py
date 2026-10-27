"""Entity knowledge-graph routes."""

from __future__ import annotations


from fastapi import Depends, APIRouter, HTTPException

from server.routes.deps import get_engine
from server.routes.models import (
    EntityGraphStatsResponse,
    EntityListResponse,
    EntityProfileResponse,
    EntityReindexResponse,
)

router = APIRouter()


@router.post("/api/entities/reindex", response_model=EntityReindexResponse)
async def reindex_entities(engine=Depends(get_engine)):
    """Rebuild the persistent entity graph from every stored memory."""
    return await engine.reindex_entities()


@router.get("/api/entities/stats", response_model=EntityGraphStatsResponse)
async def entity_graph_stats(engine=Depends(get_engine)):
    """Counts of persisted entities by type."""
    return await engine.entity_graph_stats()


@router.get("/api/entities", response_model=EntityListResponse)
async def list_entities_graph(
    type: str = "",
    limit: int = 200,
    offset: int = 0,
    q: str = "",
    engine=Depends(get_engine),
):
    """Persisted entities (optionally filtered by type and name substring),
    most-mentioned first."""
    return {
        "entities": await engine.list_entities_graph(
            entity_type=type or None, limit=limit, offset=offset, query=q or None
        )
    }


@router.get("/api/entities/search", response_model=EntityListResponse)
async def search_entities(
    q: str = "",
    type: str = "",
    limit: int = 20,
    offset: int = 0,
    engine=Depends(get_engine),
):
    """Search persisted entities by name or key, for the graph page's search
    box (#569). A thin alias over ``GET /api/entities`` with the query named
    ``q`` and a smaller default page, kept as its own path because the page's
    call site reads as a search, not a list.

    Registered **before** ``/api/entities/{entity_id:path}`` so the literal
    path is not swallowed by the catch-all (the same reason ``/stats`` sits
    above it)."""
    return {
        "entities": await engine.list_entities_graph(
            entity_type=type or None, limit=limit, offset=offset, query=q or None
        )
    }


@router.get("/api/entities/{entity_id:path}", response_model=EntityProfileResponse)
async def get_entity(entity_id: str, engine=Depends(get_engine)):
    """An entity's profile: the memories that mention it and the entities it
    co-occurs with. ``entity_id`` may be a full id like ``person:alice@acme.com``
    or a free-text query (resolved by best match)."""
    result = await engine.get_entity(entity_id)
    if result is None:
        raise HTTPException(status_code=404, detail="entity not found")
    return result
