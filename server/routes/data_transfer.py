"""Export, backup and restore routes."""

from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from server.routes.deps import get_engine
from server.routes.models import BackupRequest, FileImportRequest, RestoreRequest
from server.routes.deps import APP_VERSION

router = APIRouter()


def _export_filename(ext: str) -> str:

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"levh-full-export-{stamp}.{ext}"


@router.get("/api/export/full.json")
async def export_full_json():
    """One-shot audit bundle: memories, entity graph, trust scores, and
    conflict candidates — the raw machine-readable record."""
    from server.core.full_export import build_full_export

    engine = await get_engine()
    export = await build_full_export(engine)
    import json as _json

    return Response(
        content=_json.dumps(export, default=str),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{_export_filename("json")}"'},
    )


@router.get("/api/export/full.sqlite")
async def export_full_sqlite():
    """Raw SQLite copy of the live database, taken via the online backup API."""
    from server.core.full_export import export_full_sqlite as export_sqlite

    engine = await get_engine()
    try:
        blob = await export_sqlite(engine)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return Response(
        content=blob,
        media_type="application/vnd.sqlite3",
        headers={"Content-Disposition": f'attachment; filename="{_export_filename("sqlite")}"'},
    )


@router.get("/api/export/full.pdf")
async def export_full_pdf():
    """Human-readable audit report (summary counts, entity/trust/conflict
    overview) rendered from the same data as the JSON export."""
    from server.core.full_export import PdfUnavailableError, build_full_export, render_full_export_pdf

    engine = await get_engine()
    export = await build_full_export(engine)
    try:
        blob = render_full_export_pdf(export)
    except PdfUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return Response(
        content=blob,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{_export_filename("pdf")}"'},
    )


@router.post("/api/backup")
async def create_backup(req: BackupRequest):
    """Full portable snapshot (all memories + sessions) as a downloadable
    file. When ``passphrase`` is set the file is encrypted at rest
    (AES-128 via Fernet, PBKDF2-derived key); otherwise it's plain JSON.
    Returns the raw bytes with a suggested filename."""

    from server.core import backup as backup_mod
    from server.core.crypto import CryptoUnavailableError

    engine = await get_engine()
    snapshot = await engine.backup(app_version=APP_VERSION)
    try:
        blob = backup_mod.make_backup_blob(snapshot, passphrase=req.passphrase or None)
    except CryptoUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    encrypted = bool(req.passphrase)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    ext = "smbackup" if encrypted else "json"
    filename = f"levh-backup-{stamp}.{ext}"
    return Response(
        content=blob,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Backup-Encrypted": "1" if encrypted else "0",
            "X-Backup-Memories": str(snapshot["counts"]["memories"]),
            "X-Backup-Sessions": str(snapshot["counts"]["sessions"]),
        },
    )


@router.post("/api/restore")
async def restore_backup(req: RestoreRequest):
    """Restore from a backup file. ``content_b64`` is the base64-encoded
    backup bytes (encrypted or plain — auto-detected). ``passphrase`` is
    required only for encrypted files. ``replace=true`` first creates a local
    SQLite safety backup, then replaces the current store; the default merges."""

    from server.core import backup as backup_mod
    from server.core.crypto import DecryptionError

    engine = await get_engine()
    try:
        blob = base64.b64decode(req.content_b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="content_b64 is not valid base64")

    try:
        snapshot = backup_mod.read_backup_blob(blob, passphrase=req.passphrase or None)
    except DecryptionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except backup_mod.BackupError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    result = await engine.restore(snapshot, replace=req.replace)
    return result


@router.post("/api/import/file")
async def import_file(req: FileImportRequest):
    """Turn an arbitrary uploaded file into memories.

    Plain text, PDF, Word, Excel and zip archives are extracted to text and
    admitted as one or more memories (split on paragraph boundaries for long
    content). Formats with no extractor still get a memory recording that
    the file arrived, so nothing silently disappears. Same base64-in-JSON
    shape as ``/api/restore`` and ``/api/connectors/upload`` — keeps
    python-multipart out of the runtime dependency list.
    """

    from server.core.file_import import chunk_text, extract_parts

    try:
        blob = base64.b64decode(req.content_b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="content_b64 is not valid base64")
    if not blob:
        raise HTTPException(status_code=400, detail="uploaded file is empty")

    filename = req.filename.strip() or "upload"
    parts, warnings = extract_parts(filename, blob)
    engine = await get_engine()
    tags = [*req.tags, "import:file"]

    stored = 0
    chars = 0
    if not parts:
        result = await engine.admit_memory(
            content=f"Uploaded file '{filename}' ({len(blob)} bytes) — "
            f"{'; '.join(warnings) if warnings else 'content not indexed'}.",
            tags=tags,
            project=req.project,
            source="file-import",
            metadata={"filename": filename, "bytes": len(blob), "extracted": False},
        )
        if result["stored"]:
            stored += 1
    else:
        for label, text in parts:
            chunks = chunk_text(text)
            for i, chunk in enumerate(chunks):
                chars += len(chunk)
                result = await engine.admit_memory(
                    content=chunk,
                    tags=tags,
                    project=req.project,
                    source="file-import",
                    metadata={
                        "filename": filename,
                        "part": label,
                        "chunk": i + 1,
                        "of": len(chunks),
                    },
                )
                if result["stored"]:
                    stored += 1

    return {
        "filename": filename,
        "bytes": len(blob),
        "parts": len(parts),
        "memories_created": stored,
        "chars_extracted": chars,
        "warnings": warnings,
    }
