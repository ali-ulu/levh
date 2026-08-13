"""Connector routes — import from external apps."""

from __future__ import annotations

import base64
import binascii
import os
import re
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, HTTPException

from server.routes.deps import get_engine
from server.routes.models import ConnectorRequest, ConnectorUploadRequest
from server.routes.deps import logger

router = APIRouter()


def _safe_upload_name(filename: str) -> str:
    """Reduce *filename* to a plain name that cannot escape the upload dir."""
    name = PurePosixPath(filename.replace("\\", "/")).name.strip()
    if not name or name in {".", ".."}:
        raise HTTPException(status_code=400, detail="filename is required")
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", name).lstrip(".")
    if not cleaned:
        raise HTTPException(status_code=400, detail="filename has no usable characters")
    return cleaned[:120]


def _connector_upload_dir() -> Path:
    from server.core.runtime_config import resolve_runtime_config

    base = Path(resolve_runtime_config().database_path).resolve().parent
    target = base / "uploads"
    target.mkdir(parents=True, exist_ok=True)
    return target


# A browser never hands out the absolute path of a picked file, so the
# dashboard cannot fill in ics_path/mbox_path/transcript_path from a file
# input on its own. It uploads the bytes here instead and gets back the path
# the connector should read — the server is local, so this stays on one
# machine. Same base64-in-JSON shape as /api/restore, which keeps
# python-multipart out of the runtime dependency list.
MAX_UPLOAD_BYTES = 64 * 1024 * 1024


@router.post("/api/connectors/import")
async def connector_import(req: ConnectorRequest):
    """Import data from an external app via connector."""
    from server.connectors import get_connector

    try:
        conn = get_connector(req.connector)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))

    # Connect
    try:
        await conn.connect(req.config)
    except (FileNotFoundError, ValueError, ConnectionError) as e:
        raise HTTPException(status_code=400, detail=f"Connection failed: {e}")

    # Fetch
    try:
        items = await conn.fetch(**req.params)
    except Exception as e:
        await conn.disconnect()
        # Log the full error server-side, but don't echo it back — connector
        # exceptions can embed tokens/URLs from the upstream request.
        logger.exception("connector '%s' fetch failed", req.connector)
        raise HTTPException(
            status_code=502,
            detail=f"Fetch from connector '{req.connector}' failed. See server logs.",
        )

    # Legacy import surface is still admission-gated. Connector v2 adds
    # incremental cursors, but both paths share dedupe/redaction guarantees.
    engine = await get_engine()
    try:
        result = await engine.ingest_items(
            items,
            connector=req.connector,
            project=req.project,
            use_gate=True,
        )
    finally:
        await conn.disconnect()
    return result


@router.post("/api/connectors/sync")
async def connector_sync(req: ConnectorRequest):
    """Connector v2 ingest: fetch, then route items through the admission
    gate (dedupe + secret redaction), with incremental sync bookkeeping."""
    from server.connectors import get_connector

    try:
        conn = get_connector(req.connector)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))

    try:
        await conn.connect(req.config)
    except (FileNotFoundError, ValueError, ConnectionError) as e:
        raise HTTPException(status_code=400, detail=f"Connection failed: {e}")

    try:
        items = await conn.fetch(**req.params)
    except Exception:
        await conn.disconnect()
        logger.exception("connector '%s' fetch failed", req.connector)
        raise HTTPException(
            status_code=502,
            detail=f"Fetch from connector '{req.connector}' failed. See server logs.",
        )

    engine = await get_engine()
    result = await engine.ingest_items(
        items, connector=req.connector, project=req.project, use_gate=req.use_gate
    )
    await conn.disconnect()
    return result


@router.post("/api/connectors/upload")
async def connector_upload(req: ConnectorUploadRequest):
    """Store an uploaded file locally and return the path to import from."""

    name = _safe_upload_name(req.filename)
    try:
        blob = base64.b64decode(req.content_b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="content_b64 is not valid base64")
    if not blob:
        raise HTTPException(status_code=400, detail="uploaded file is empty")
    if len(blob) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"file is larger than the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB upload limit",
        )

    target = _connector_upload_dir() / name
    stem, suffix = os.path.splitext(name)
    counter = 1
    while target.exists():
        target = target.with_name(f"{stem}-{counter}{suffix}")
        counter += 1
    target.write_bytes(blob)
    return {"path": str(target), "filename": target.name, "bytes": len(blob)}


@router.get("/api/connectors/sync-state")
async def connector_sync_state():
    engine = await get_engine()
    return {"sync_state": await engine.list_sync_state()}


@router.get("/api/connectors")
async def list_connectors():
    """List available connectors and their status."""
    from server.connectors import list_connectors as _list

    return {"connectors": _list()}


@router.get("/api/connectors/{name}/config")
async def get_connector_config(name: str):
    """Get required config fields for a connector."""
    from server.connectors import get_connector

    try:
        conn = get_connector(name)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return {
        "name": conn.name,
        "description": conn.description,
        "required_config_keys": conn.required_config_keys(),
        "help": conn.help_text(),
    }
