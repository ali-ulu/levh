"""Whole-database operations: safety backups and restore.

A slice of :class:`server.core.database.Database`, split out to keep each
file readable. Bodies are unchanged from the single-file version.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from ..env import get_env



class SnapshotQueries:
    """Whole-database operations: safety backups and restore."""

    async def create_safety_backup(self, destination: str | None = None) -> str | None:
        """Create a consistent SQLite safety copy before destructive restore.

        Uses SQLite's online backup API, so WAL pages are included correctly.
        In-memory databases have no durable location and therefore return None.
        """
        if self.db_path == ":memory:":
            return None

        source = Path(self.db_path).expanduser().resolve()
        if destination:
            target = Path(destination).expanduser().resolve()
        else:
            configured_dir = get_env("LEVH_SAFETY_BACKUP_DIR", "").strip()
            backup_dir = (
                Path(configured_dir).expanduser().resolve()
                if configured_dir
                else source.parent / "safety-backups"
            )
            backup_dir.mkdir(parents=True, exist_ok=True)
            try:
                backup_dir.chmod(0o700)
            except OSError:
                pass
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
            target = backup_dir / f"stackmemory-pre-restore-{stamp}.db"

        target.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(str(target)) as destination_conn:
            await self.conn.backup(destination_conn)
        try:
            target.chmod(0o600)
        except OSError:
            pass
        return str(target)

    async def restore_snapshot_transaction(
        self,
        memories: list[dict],
        sessions: list[dict],
        replace: bool,
        attachments: list[dict] | None = None,
    ) -> None:
        """Atomically merge or replace a fully validated snapshot.

        Callers must validate every record before entering this method.  No
        destructive clear occurs until all validation has succeeded.
        """
        attachments = attachments or []
        await self.conn.execute("BEGIN IMMEDIATE")
        try:
            memory_ids = [str(m["id"]) for m in memories]
            if replace:
                await self.conn.execute("DELETE FROM memory_conflict_candidates")
                await self.conn.execute("DELETE FROM memory_trust_scores")
                await self.conn.execute("DELETE FROM memory_entities")
                await self.conn.execute("DELETE FROM entities")
                # attachments cascades from memories via ON DELETE CASCADE
                await self.conn.execute("DELETE FROM memories")
                await self.conn.execute("DELETE FROM sessions")
            elif memory_ids:
                placeholders = ",".join("?" for _ in memory_ids)
                await self.conn.execute(
                    f"DELETE FROM memory_conflict_candidates "
                    f"WHERE memory_id_a IN ({placeholders}) OR memory_id_b IN ({placeholders})",
                    [*memory_ids, *memory_ids],
                )
                await self.conn.execute(
                    f"DELETE FROM memory_trust_scores WHERE memory_id IN ({placeholders})",
                    memory_ids,
                )
                await self.conn.execute(
                    f"DELETE FROM memory_entities WHERE memory_id IN ({placeholders})",
                    memory_ids,
                )

            for session in sessions:
                await self.conn.execute(
                    """
                    INSERT OR REPLACE INTO sessions
                        (id, name, status, metadata, memory_count, created_at, ended_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session["id"],
                        session.get("name", ""),
                        session.get("status", "active"),
                        json.dumps(session.get("metadata", {}) or {}),
                        int(session.get("memory_count", 0)),
                        session.get("created_at"),
                        session.get("ended_at"),
                    ),
                )

            for memory in memories:
                await self.conn.execute(
                    """
                    INSERT OR REPLACE INTO memories
                        (id, content, memory_type, embedding, importance, frequency,
                         tags, session_id, project, source, pinned, metadata, hscore,
                         created_at, accessed_at, decay_factor, stability_hours, recall_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        memory["id"],
                        memory["content"],
                        str(memory.get("memory_type", "short_term")),
                        json.dumps(memory.get("embedding")) if memory.get("embedding") else None,
                        float(memory.get("importance", 0.5)),
                        int(memory.get("frequency", 1)),
                        json.dumps(memory.get("tags", []) or []),
                        memory.get("session_id"),
                        memory.get("project"),
                        memory.get("source"),
                        1 if memory.get("pinned") else 0,
                        json.dumps(memory.get("metadata", {}) or {}),
                        memory.get("hscore"),
                        memory["created_at"],
                        memory["accessed_at"],
                        float(memory.get("decay_factor", 1.0)),
                        float(memory.get("stability_hours", 168.0)),
                        int(memory.get("recall_count", 0)),
                    ),
                )

            for attachment in attachments:
                await self.conn.execute(
                    """
                    INSERT OR REPLACE INTO attachments
                        (id, memory_id, path, sha256, mime, size, derived_text,
                         derived_by, status, created_at, verified_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        attachment["id"],
                        attachment["memory_id"],
                        attachment["path"],
                        attachment["sha256"],
                        attachment.get("mime"),
                        int(attachment.get("size", 0)),
                        attachment.get("derived_text"),
                        attachment.get("derived_by", "none"),
                        attachment.get("status", "ok"),
                        attachment["created_at"],
                        attachment.get("verified_at"),
                    ),
                )

            # Session counts are derived from actual restored rows, never
            # trusted from a potentially stale snapshot field.
            await self.conn.execute(
                """
                UPDATE sessions
                SET memory_count = (
                    SELECT COUNT(*) FROM memories WHERE memories.session_id = sessions.id
                )
                """
            )
            await self.conn.execute(
                "DELETE FROM entities WHERE id NOT IN "
                "(SELECT DISTINCT entity_id FROM memory_entities)"
            )
            await self.conn.commit()
        except Exception:
            await self.conn.rollback()
            raise
