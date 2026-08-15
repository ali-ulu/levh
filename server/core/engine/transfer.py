"""Export, import, backup and restore.

Part of :class:`server.core.memory_engine.MemoryEngine`, split out to keep
each file readable. Mixins rather than separate services: the methods use the
engine's own state throughout, and moving the bodies unchanged is what makes
the split verifiable.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..types import (
    Memory,
    MemoryType,
    Session,
)


class MemoryTransferMixin:
    """Export, import, backup and restore."""

    async def export_memories(self, session_id: str | None = None) -> list[dict]:
        filters = {}
        if session_id:
            filters["session_id"] = session_id
        memories = await self.episodic.search(**filters, limit=10000)
        return [m.model_dump() for m in memories]

    async def import_memories(self, data: list[dict]) -> int:
        """Restore trusted, already-normalized memory records exactly.

        This low-level path preserves IDs, timestamps and lifecycle state and is
        therefore reserved for backup/restore and internal compatibility.  User
        facing JSON imports must use :meth:`import_memories_gated` so content is
        evaluated by the admission policy before persistence.

        SQLite is written *before* in-memory caches.  A failed DB write can no
        longer leave a ghost memory that is recallable until process restart.
        """
        count = 0
        skipped = 0
        for item in data:
            try:
                mem = Memory(**item)
                await self.episodic.store(mem)
                if mem.memory_type == MemoryType.SHORT_TERM:
                    self.short_term.add(mem)
                if mem.embedding:
                    self.vector_store.add(mem)
                count += 1
            except Exception:
                # Malformed record — skip it but keep a count so a partially
                # bad import file is visible instead of silently swallowed.
                skipped += 1
                continue
        if count or skipped:
            if count:
                self._mark_derived_dirty()
            self._emit("imported", {"count": count, "skipped": skipped, "gated": False})
        return count

    async def import_memories_gated(self, data: list[dict]) -> dict:
        """Import user-supplied JSON through the deterministic admission gate.

        The record's portable identity and lifecycle fields are preserved, but
        untrusted embeddings are discarded and recomputed from the admitted
        (possibly redacted) content using the active embedder.  Reject/review
        decisions are not persisted.  Each item is isolated and the returned
        breakdown makes partial imports explicit.
        """
        imported = redacted = duplicates = held = errors = 0

        for item in data:
            try:
                mem = Memory(**item)
                decision = await self.evaluate_admission(
                    mem.content, project=mem.project
                )
                action = decision["action"]
                if action in ("reject", "review"):
                    if action == "review":
                        held += 1
                    else:
                        duplicates += 1
                    continue

                content = (
                    decision["redacted_content"]
                    if decision["redacted"]
                    else mem.content
                )
                metadata = dict(mem.metadata or {})
                metadata["admission"] = {
                    "action": action,
                    "reasons": decision["reasons"],
                    "reason_codes": decision["reason_codes"],
                    "redacted": decision["redacted"],
                    "secrets": decision["secrets"],
                    "max_similarity": decision["max_similarity"],
                    "forced": False,
                }
                metadata["imported_via"] = "json"

                # Never trust an imported vector.  It may be stale, poisoned or
                # from a different embedding dimension/model.
                embedding = await self.embedder.embed(content)
                metadata["embedding_provenance"] = self.embedder.identity()
                mem = mem.model_copy(
                    update={
                        "content": content,
                        "embedding": embedding,
                        "metadata": metadata,
                    }
                )

                # Persist first, then expose through process-local caches.
                await self.episodic.store(mem)
                if mem.memory_type == MemoryType.SHORT_TERM:
                    self.short_term.add(mem)
                self.vector_store.add(mem)
                await self._apply_interference(mem)
                if mem.session_id:
                    await self._refresh_session_count(mem.session_id)

                imported += 1
                if decision["redacted"]:
                    redacted += 1
            except Exception:
                errors += 1
                continue

        result = {
            "imported": imported,
            "redacted": redacted,
            "duplicates": duplicates,
            "held": held,
            "errors": errors,
            "gated": True,
        }
        if any((imported, redacted, duplicates, held, errors)):
            if imported:
                self._mark_derived_dirty()
            self._emit("imported", result)
        return result

    async def backup(self, app_version: str = "") -> dict:
        """Build a full portable snapshot: every memory (with its complete
        decay state) plus every session. The returned dict is the plain
        snapshot; encrypting it into a file blob is the caller's job (see
        ``server.core.backup.make_backup_blob``)."""
        from ..backup import make_snapshot

        memories = await self.episodic.search(limit=1_000_000)
        mem_dicts = [m.model_dump() for m in memories]
        sessions = await self.db.get_all_sessions(limit=1_000_000)
        attachments = await self.db.all_attachments()
        created_at = datetime.now(timezone.utc).isoformat()
        return make_snapshot(mem_dicts, sessions, app_version, created_at, attachments=attachments)

    async def restore(self, snapshot: dict, replace: bool = False) -> dict:
        """Atomically restore a fully validated backup snapshot.

        Validation is completed for every memory and session before a replace
        can delete current data.  The SQLite merge/replace is one transaction;
        caches and all derived graph/trust/conflict state are rebuilt only after
        commit.  Malformed snapshots fail closed with the existing store intact.
        """
        from ..backup import BACKUP_FORMAT
        from ..types import Session

        if not isinstance(snapshot, dict) or snapshot.get("format") != BACKUP_FORMAT:
            raise ValueError("not a LEVH backup snapshot")

        raw_memories = snapshot.get("memories")
        raw_sessions = snapshot.get("sessions")
        raw_attachments = snapshot.get("attachments", [])
        if not isinstance(raw_memories, list) or not isinstance(raw_sessions, list):
            raise ValueError("backup snapshot memories/sessions must be arrays")
        if not isinstance(raw_attachments, list):
            raise ValueError("backup snapshot attachments must be an array")

        memories: list[Memory] = []
        sessions: list[Session] = []
        attachments: list[dict] = []
        try:
            for index, item in enumerate(raw_memories):
                if not isinstance(item, dict):
                    raise ValueError(f"memory[{index}] is not an object")
                memories.append(Memory(**item))
            for index, item in enumerate(raw_sessions):
                if not isinstance(item, dict):
                    raise ValueError(f"session[{index}] is not an object")
                sessions.append(Session(**item))
            memory_id_set = {m.id for m in memories}
            for index, item in enumerate(raw_attachments):
                if not isinstance(item, dict):
                    raise ValueError(f"attachment[{index}] is not an object")
                required = {"id", "memory_id", "path", "sha256", "size", "created_at"}
                if not required.issubset(item):
                    raise ValueError(f"attachment[{index}] is missing required fields")
                if item["memory_id"] not in memory_id_set:
                    raise ValueError(f"attachment[{index}] references an unknown memory")
                attachments.append(item)
        except Exception as exc:
            raise ValueError(f"invalid backup snapshot record: {exc}") from exc

        memory_ids = [m.id for m in memories]
        session_ids = [s.id for s in sessions]
        attachment_ids = [a["id"] for a in attachments]
        if len(memory_ids) != len(set(memory_ids)):
            raise ValueError("backup snapshot contains duplicate memory ids")
        if len(session_ids) != len(set(session_ids)):
            raise ValueError("backup snapshot contains duplicate session ids")
        if len(attachment_ids) != len(set(attachment_ids)):
            raise ValueError("backup snapshot contains duplicate attachment ids")

        safety_backup_path: str | None = None
        if replace:
            existing_items = await self.db.count_memories() + await self.db.count_sessions()
            if existing_items:
                # Fail closed: a destructive replace is not allowed to proceed
                # unless the current SQLite state has first been copied with
                # SQLite's online-backup API. In-memory test stores have no
                # durable location and intentionally return None.
                safety_backup_path = await self.db.create_safety_backup()

        await self.db.restore_snapshot_transaction(
            [m.model_dump(mode="json") for m in memories],
            [s.model_dump(mode="json") for s in sessions],
            replace=replace,
            attachments=attachments,
        )

        # Rebuild process-local state from committed SQLite, never from the
        # untrusted snapshot objects.
        self.short_term.clear()
        self.vector_store.clear()
        restored_all = await self.episodic.get_all(limit=1_000_000)
        for memory in restored_all:
            if memory.memory_type == MemoryType.SHORT_TERM:
                self.short_term.add(memory)
            if memory.embedding:
                self.vector_store.add(memory)

        self._derived_dirty = True
        await self._ensure_derived_state()

        self._emit(
            "restored",
            {
                "memories": len(memories),
                "sessions": len(sessions),
                "attachments": len(attachments),
                "replace": replace,
            },
        )
        return {
            "memories": len(memories),
            "sessions": len(sessions),
            "attachments": len(attachments),
            "replace": replace,
            "safety_backup_path": safety_backup_path,
        }
