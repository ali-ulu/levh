"""Context routes — the compiled context window and context files."""

from __future__ import annotations


from fastapi import APIRouter

from server.routes.deps import get_engine
from server.routes.models import ContextFileRequest

router = APIRouter()


@router.get("/api/context")
async def get_context(session_id: str = "", project: str = "", max_tokens: int = 4000):
    engine = await get_engine()
    context = await engine.get_context(
        session_id=session_id or None,
        project=project or None,
        max_tokens=max_tokens,
    )
    return {"context": context, "chars": len(context)}


@router.post("/api/context-file")
async def generate_context_file(req: ContextFileRequest):
    """Generate a CLAUDE.md / .cursorrules style context file from memories."""
    engine = await get_engine()
    content = await engine.generate_context_file(
        project=req.project or None, style=req.style
    )
    filename = "CLAUDE.md" if req.style == "claude" else ".cursorrules"
    return {"filename": filename, "content": content}
