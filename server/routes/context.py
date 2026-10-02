"""Context routes — the compiled context window and context files."""

from __future__ import annotations


from fastapi import Depends, APIRouter

from server.routes.deps import get_engine
from server.routes.models import ContextFileRequest, ContextFileResponse, ContextResponse

router = APIRouter()


@router.get("/api/context", response_model=ContextResponse)
async def get_context(
    session_id: str = "",
    project: str = "",
    max_tokens: int = 4000,
    query: str = "",
    engine=Depends(get_engine),
):
    context = await engine.get_context(
        session_id=session_id or None,
        project=project or None,
        max_tokens=max_tokens,
        query=query or None,
    )
    return {"context": context, "chars": len(context)}


@router.post("/api/context-file", response_model=ContextFileResponse)
async def generate_context_file(req: ContextFileRequest, engine=Depends(get_engine)):
    """Generate a CLAUDE.md / .cursorrules style context file from memories."""
    content = await engine.generate_context_file(
        project=req.project or None, style=req.style
    )
    filename = "CLAUDE.md" if req.style == "claude" else ".cursorrules"
    return {"filename": filename, "content": content}
