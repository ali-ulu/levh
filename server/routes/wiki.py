"""REST surface for building, searching and linting the local LLM Wiki."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from server.core.llm_wiki import LLMWiki, WikiError
from server.core.tenancy import AuthorizationError, authorize
from server.routes.deps import get_engine
from server.routes.models import (
    WikiBuildRequest,
    WikiBuildResponse,
    WikiLintResponse,
    WikiPageResponse,
    WikiSearchResponse,
    WikiStatusResponse,
)

router = APIRouter()


def _service(engine) -> LLMWiki:
    try:
        return LLMWiki(engine)
    except WikiError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/wiki/status", response_model=WikiStatusResponse)
async def wiki_status(engine=Depends(get_engine)):
    """Report Wiki state without creating or changing any files."""
    try:
        authorize("read")
        return _service(engine).status()
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except WikiError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/wiki/build", response_model=WikiBuildResponse)
async def build_wiki(request: WikiBuildRequest, engine=Depends(get_engine)):
    """Generate source snapshots and Markdown evidence pages; LLM drafts are opt-in."""
    service = _service(engine)
    try:
        return await service.build(llm_summaries=request.llm_summaries)
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except WikiError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/api/wiki/lint", response_model=WikiLintResponse)
async def lint_wiki(engine=Depends(get_engine)):
    """Check evidence digests, page manifests and internal Markdown links."""
    try:
        authorize("read")
        return _service(engine).lint()
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except WikiError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/api/wiki/search", response_model=WikiSearchResponse)
async def search_wiki(q: str, limit: int = 10, engine=Depends(get_engine)):
    """Search the generated Markdown pages without changing memories or Wiki files."""
    try:
        authorize("read")
        return _service(engine).search(q, limit)
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except WikiError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/wiki/pages/{page_id}", response_model=WikiPageResponse)
async def get_wiki_page(page_id: str, engine=Depends(get_engine)):
    """Read one manifest-indexed Wiki page by its opaque page ID."""
    try:
        authorize("read")
        page = _service(engine).get_page(page_id)
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except WikiError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if page is None:
        raise HTTPException(status_code=404, detail="Wiki page not found")
    return page
