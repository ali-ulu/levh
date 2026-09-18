"""Export, import, backup and restore.

Part of :class:`server.core.memory_engine.MemoryEngine`, split out to keep
each file readable. Mixins rather than separate services: the methods use the
engine's own state throughout, and moving the bodies unchanged is what makes
the split verifiable.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..restore_service import RestoreService
from ..types import (
    Memory,
    MemoryType,
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
            except Exception:  # noqa: BLE001 - one bad row counts as an error and the export continues
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
        """Delegate to the RestoreService (issue #95 proposal 3).

        The flow — validation, safety backup, one transaction, cache
        rebuild — lives in ``server.core.restore_service``; the engine
        keeps the public method so call sites are unchanged.
        """
        return await RestoreService(self).restore(snapshot, replace=replace)
