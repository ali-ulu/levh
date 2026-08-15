"""Attachment routes — files bound to a memory as evidence, not content.

The memory stays text; the file lives on disk and is referenced by path +
sha256 (see server/core/engine/attachments.py). A browser never exposes the
absolute path of a picked file, so the dashboard uploads bytes here first and
gets back a path to pass to the attach endpoint — same base64-in-JSON shape
as /api/restore and /api/connectors/upload, which keeps python-multipart out
of the runtime dependency list. CLI/MCP callers with a local path can skip
the upload step and attach directly.
"""

from __future__ import annotations

import base64
import binascii
import os
import re
import uuid
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, HTTPException

from server.routes.deps import get_engine
from server.routes.models import AttachFileRequest, AttachmentUploadRequest

router = APIRouter()

# No size limit is enforced here — attachments are meant to hold anything
# from a screenshot to a meeting recording, and the app itself imposes no cap.

# The on-disk suffix comes from this fixed table's VALUES, keyed by the
# upload's own extension — never from a transform of the request, and not
# even from the tainted extension variable itself once matched. A regex
# substitution, or even `x if x in allowed_set else ""`, still carries taint
# as far as CodeQL's py/path-injection query is concerned: the assigned value
# is structurally the same tainted variable, guard or not. A dict lookup that
# *returns* one of its own hardcoded values is what actually breaks the
# taint — the exact pattern connector uploads already use (see
# server/routes/connectors.py UPLOAD_SUFFIXES / _stored_suffix).
SAFE_UPLOAD_SUFFIXES = {
    ".txt": ".txt", ".md": ".md", ".markdown": ".markdown", ".csv": ".csv",
    ".tsv": ".tsv", ".json": ".json", ".log": ".log", ".yaml": ".yaml", ".yml": ".yml",
    ".html": ".html", ".htm": ".htm", ".xml": ".xml", ".rtf": ".rtf",
    ".pdf": ".pdf", ".doc": ".doc", ".docx": ".docx", ".xls": ".xls", ".xlsx": ".xlsx",
    ".ppt": ".ppt", ".pptx": ".pptx", ".odt": ".odt", ".ods": ".ods", ".odp": ".odp",
    ".png": ".png", ".jpg": ".jpg", ".jpeg": ".jpeg", ".gif": ".gif", ".webp": ".webp",
    ".bmp": ".bmp", ".tiff": ".tiff", ".heic": ".heic", ".svg": ".svg",
    ".mp4": ".mp4", ".mov": ".mov", ".mkv": ".mkv", ".avi": ".avi", ".webm": ".webm",
    ".mp3": ".mp3", ".wav": ".wav", ".m4a": ".m4a", ".ogg": ".ogg", ".flac": ".flac",
    ".zip": ".zip",
}


def _display_name(filename: str) -> str:
    """The name shown back to the user. Never used to build a path."""
    name = PurePosixPath(filename.replace("\\", "/")).name.strip()
    if not name or name in {".", ".."}:
        raise HTTPException(status_code=400, detail="filename is required")
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", name).lstrip(".")
    if not cleaned:
        raise HTTPException(status_code=400, detail="filename has no usable characters")
    return cleaned[:120]


def _attachments_dir() -> Path:
    from server.core.runtime_config import resolve_runtime_config

    base = Path(resolve_runtime_config().database_path).resolve().parent
    target = base / "attachments"
    target.mkdir(parents=True, exist_ok=True)
    return target


@router.post("/api/attachments/upload")
async def upload_attachment(req: AttachmentUploadRequest):
    """Store an uploaded file locally and return the path to attach from."""

    display = _display_name(req.filename)
    try:
        blob = base64.b64decode(req.content_b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="content_b64 is not valid base64")
    if not blob:
        raise HTTPException(status_code=400, detail="uploaded file is empty")

    # The suffix is kept when recognized (any common file type is welcome —
    # image, video, PDF, ...) but the basename is always a fresh identifier,
    # so nothing about the on-disk path is built from the request.
    suffix = SAFE_UPLOAD_SUFFIXES.get(os.path.splitext(display)[1].lower(), "")
    target = _attachments_dir() / f"{uuid.uuid4().hex}{suffix}"
    target.write_bytes(blob)
    return {"path": str(target), "filename": display, "bytes": len(blob)}


@router.post("/api/memories/{memory_id}/attachments")
async def attach_file(memory_id: str, req: AttachFileRequest):
    """Attach a local file to a memory by reference (path + sha256), with
    optional derived text (OCR/transcript/caption) that recall actually
    searches over."""
    engine = await get_engine()
    try:
        return await engine.attach_file(
            memory_id, req.path, derived_text=req.derived_text, derived_by=req.derived_by
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/memories/{memory_id}/attachments")
async def list_memory_attachments(memory_id: str):
    engine = await get_engine()
    return {"attachments": await engine.list_memory_attachments(memory_id)}


@router.post("/api/attachments/{attachment_id}/verify")
async def verify_attachment(attachment_id: str):
    """Re-check the file against what was recorded at attach time. A missing
    or changed file raises a conflict candidate rather than silently altering
    the memory — see /api/conflicts."""
    engine = await get_engine()
    try:
        return await engine.verify_attachment(attachment_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/api/attachments/verify-all")
async def verify_all_attachments():
    engine = await get_engine()
    return await engine.verify_all_attachments()


@router.delete("/api/attachments/{attachment_id}")
async def delete_attachment(attachment_id: str):
    engine = await get_engine()
    deleted = await engine.delete_attachment(attachment_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"attachment '{attachment_id}' not found")
    return {"deleted": True}
