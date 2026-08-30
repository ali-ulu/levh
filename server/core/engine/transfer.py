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


# A backup is a JSON envelope, so a carried file is base64 inside it — roughly
# a third larger than the file. The ceiling keeps one long video from turning a
# memory backup into something nobody can open, and anything over it is
# recorded as skipped rather than dropped.
MAX_CARRIED_ATTACHMENT_BYTES = 25 * 1024 * 1024


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
        (possibly redacted) content using the active embedder.  Rejected items
        are dropped; ``review`` items are held for a human (see
        ``hold_for_review``) rather than discarded, so an import cannot silently
        lose the half of a file the gate declined to decide on.  Each item is
        isolated and the returned breakdown makes partial imports explicit.
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
                        await self.hold_for_review(
                            content=mem.content,
                            decision=decision,
                            importance=mem.importance,
                            tags=mem.tags,
                            session_id=mem.session_id,
                            project=mem.project,
                            source=mem.source,
                            pinned=mem.pinned,
                            memory_type=mem.memory_type.value,
                            metadata=mem.metadata,
                        )
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

    async def backup(
        self,
        app_version: str = "",
        max_attachment_bytes: int = MAX_CARRIED_ATTACHMENT_BYTES,
    ) -> dict:
        """Build a full portable snapshot: every memory (with its complete
        decay state), every session, and the bytes of every attachment LEVH
        itself owns. The returned dict is the plain snapshot; encrypting it into
        a file blob is the caller's job (see
        ``server.core.backup.make_backup_blob``).

        Attachment rows used to travel as a path, a hash and a size, and nothing
        else. Restoring on another machine wrote the same absolute path back, so
        the record looked restored while the file it named was not there — the
        first verification pass turned it up as ``missing``. For a file LEVH
        uploaded into its own store that is data loss, because no other copy of
        it exists.

        A referenced file — one the user attached from somewhere they chose —
        deliberately still travels as a reference. Its bytes are already theirs,
        and pulling arbitrary documents into a memory backup would take more
        than was offered.

        Nothing is dropped quietly: every attachment records ``carried`` and,
        when false, the ``carry_skipped`` reason (``referenced``, ``too_large``
        or ``unreadable``), and the counts appear in the envelope.
        """
        import base64

        from ..attachment_store import is_managed
        from ..backup import make_snapshot

        memories = await self.episodic.search(limit=1_000_000)
        mem_dicts = [m.model_dump() for m in memories]
        sessions = await self.db.get_all_sessions(limit=1_000_000)
        attachments = [dict(row) for row in await self.db.all_attachments()]

        for row in attachments:
            row["carried"] = False
            if not is_managed(row.get("path") or ""):
                row["carry_skipped"] = "referenced"
                continue
            if int(row.get("size") or 0) > max_attachment_bytes:
                # Base64 in a JSON envelope is a poor container for a large
                # video. Saying so on the record beats producing a backup that
                # is quietly enormous or quietly incomplete.
                row["carry_skipped"] = "too_large"
                continue
            try:
                with open(row["path"], "rb") as handle:
                    row["content_b64"] = base64.b64encode(handle.read()).decode("ascii")
                row["carried"] = True
                row.pop("carry_skipped", None)
            except OSError:
                # Already gone or unreadable. The row still travels, so the
                # restored instance reports it missing rather than forgetting
                # the attachment ever existed.
                row["carry_skipped"] = "unreadable"

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

        # After validation, before anything destructive: a carried file that
        # cannot be written should not have cost the caller their current data.
        attachments, attachment_files = self._materialize_carried_attachments(attachments)

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
                **attachment_files,
            },
        )
        return {
            "memories": len(memories),
            "sessions": len(sessions),
            "attachments": len(attachments),
            "replace": replace,
            "safety_backup_path": safety_backup_path,
            **attachment_files,
        }

    @staticmethod
    def _materialize_carried_attachments(attachments: list[dict]) -> tuple[list[dict], dict]:
        """Write back the attachment files a snapshot carried, and rewrite each
        row to point at the copy this instance now owns.

        A carried file cannot keep the path it had on the machine that made the
        backup -- that path belongs to another database directory, and on a
        clean install it does not exist at all. It is written into *this*
        instance's store under a fresh name, and the row's ``path`` is rewritten
        to match. Everything else about the row, ``sha256`` included, is left
        alone: it is the hash of what was attached, and it is what a later
        verification pass compares against.

        The bytes are checked against that hash before they are written. A
        snapshot is untrusted input, and restoring a file whose content does not
        match the hash the row asserts would manufacture a ``changed``
        attachment out of a backup the user believed was intact.

        Rows carrying no bytes pass through untouched: a referenced file is
        still expected at its own path, which is the correct behaviour for a
        file the user owns.
        """
        import base64
        import binascii
        import hashlib
        import os
        import uuid

        from ..attachment_store import attachments_dir

        restored: list[dict] = []
        written = failed = referenced = 0

        for row in attachments:
            row = dict(row)
            encoded = row.pop("content_b64", None)
            row.pop("carried", None)
            row.pop("carry_skipped", None)
            if not encoded:
                referenced += 1
                restored.append(row)
                continue
            try:
                blob = base64.b64decode(encoded, validate=True)
                if hashlib.sha256(blob).hexdigest() != row["sha256"]:
                    raise ValueError("carried bytes do not match the recorded sha256")
                suffix = os.path.splitext(row.get("path") or "")[1]
                target = attachments_dir() / f"{uuid.uuid4().hex}{suffix}"
                target.write_bytes(blob)
                row["path"] = str(target)
                written += 1
            except (binascii.Error, ValueError, OSError):
                # The row still lands, pointing where it did. The attachment is
                # then reported missing by a verify pass -- visible, which is
                # the whole difference from the behaviour being fixed here.
                failed += 1
            restored.append(row)

        return restored, {
            "attachment_files_written": written,
            "attachment_files_failed": failed,
            "attachments_by_reference": referenced,
        }
