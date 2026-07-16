"""Tools: create_backup / restore_backup — full portable snapshot of every
memory and session, optionally encrypted at rest with a passphrase.

Since the MCP server runs locally, these read/write a file on the local
filesystem by path — the natural fit for a local-first tool."""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from server.core import backup as backup_mod
from server.core.crypto import CryptoUnavailableError, DecryptionError
from server.core.memory_engine import MemoryEngine


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    @mcp.tool()
    async def create_backup(path: str, passphrase: str = "") -> str:
        """Write a full backup (every memory + session, with decay state) to a
        local file. Provide a passphrase to encrypt it at rest.

        Args:
            path: Where to write the backup file (e.g. "~/stackmemory-backup.json",
                or ".smbackup" when encrypted).
            passphrase: Optional. When set, the file is encrypted (AES-128 via
                Fernet, PBKDF2-derived key) and can only be restored with the
                same passphrase — keep it safe, it cannot be recovered.
        """
        snapshot = await engine.backup()
        try:
            blob = backup_mod.make_backup_blob(snapshot, passphrase=passphrase or None)
        except CryptoUnavailableError as exc:
            return f"Cannot encrypt: {exc}"

        dest = os.path.abspath(os.path.expanduser(path))
        try:
            os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
            with open(dest, "wb") as fh:
                fh.write(blob)
        except OSError as exc:
            return f"Could not write backup to {dest}: {exc}"

        c = snapshot["counts"]
        enc = " (encrypted)" if passphrase else ""
        return (
            f"Backup written to {dest}{enc}: "
            f"{c['memories']} memories, {c['sessions']} sessions."
        )

    @mcp.tool()
    async def restore_backup(path: str, passphrase: str = "", replace: bool = False) -> str:
        """Restore memories and sessions from a backup file.

        Args:
            path: The backup file to restore from.
            passphrase: Required only if the backup is encrypted.
            replace: If true, wipe the current store first so it becomes an
                exact copy of the backup. Default false (merge — same-id rows
                are overwritten, everything else is added).
        """
        src = os.path.abspath(os.path.expanduser(path))
        if not os.path.exists(src):
            return f"No backup file at {src}."
        try:
            with open(src, "rb") as fh:
                blob = fh.read()
        except OSError as exc:
            return f"Could not read {src}: {exc}"

        try:
            snapshot = backup_mod.read_backup_blob(blob, passphrase=passphrase or None)
        except DecryptionError as exc:
            return f"Restore failed: {exc}"
        except backup_mod.BackupError as exc:
            return f"Restore failed: {exc}"

        result = await engine.restore(snapshot, replace=replace)
        mode = "replaced" if replace else "merged"
        message = (
            f"Restored ({mode}) from {src}: "
            f"{result['memories']} memories, {result['sessions']} sessions."
        )
        if result.get("safety_backup_path"):
            message += f" Pre-restore safety backup: {result['safety_backup_path']}"
        return message
