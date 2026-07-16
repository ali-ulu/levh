"""Portable backup snapshots (Faz 0 security).

A backup is a single self-describing snapshot of everything a user would lose
if their database vanished: every memory (with its full decay state — stability,
recall counts, importance, pins, metadata) plus every session. It serialises to
a JSON envelope and can optionally be encrypted at rest with a passphrase
(see ``crypto.py``).

The blob is format-detecting on the way back in: an encrypted blob starts with
the crypto magic bytes; anything else is treated as plaintext JSON. So
``read_backup_blob`` handles both, and a passphrase is only required when the
file is actually encrypted.
"""

from __future__ import annotations

import json
from typing import Any

from . import crypto

BACKUP_FORMAT = "stackmemory-backup"
BACKUP_VERSION = 1


class BackupError(ValueError):
    """A blob that isn't a recognisable StackMemory backup."""


def make_snapshot(memories: list[dict], sessions: list[dict], app_version: str,
                  created_at: str) -> dict[str, Any]:
    """Assemble the snapshot envelope from already-serialised records."""
    return {
        "format": BACKUP_FORMAT,
        "backup_version": BACKUP_VERSION,
        "app_version": app_version,
        "created_at": created_at,
        "counts": {"memories": len(memories), "sessions": len(sessions)},
        "memories": memories,
        "sessions": sessions,
    }


def make_backup_blob(snapshot: dict, passphrase: str | None = None) -> bytes:
    """Serialise a snapshot to bytes, encrypting when a passphrase is given."""
    data = json.dumps(snapshot, ensure_ascii=False, default=str).encode("utf-8")
    if passphrase:
        return crypto.encrypt(data, passphrase)
    return data


def read_backup_blob(blob: bytes, passphrase: str | None = None) -> dict:
    """Parse a backup blob back into a snapshot dict.

    Detects encryption automatically. Raises :class:`BackupError` for a blob
    that is neither valid encrypted-then-JSON nor plaintext JSON, or
    :class:`crypto.DecryptionError` for a wrong passphrase.
    """
    if isinstance(blob, str):
        blob = blob.encode("utf-8")

    if crypto.is_encrypted(blob):
        if not passphrase:
            raise BackupError("this backup is encrypted — a passphrase is required")
        data = crypto.decrypt(blob, passphrase)
    else:
        data = bytes(blob)

    try:
        snapshot = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError("not a valid StackMemory backup (unreadable JSON)") from exc

    if not isinstance(snapshot, dict) or snapshot.get("format") != BACKUP_FORMAT:
        raise BackupError("not a StackMemory backup (missing format marker)")
    return snapshot
