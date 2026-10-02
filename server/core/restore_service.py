"""Restore service — the trusted, atomic snapshot-restore flow.

Extracted from the transfer mixin (issue #95 proposal 3): restore was the
heaviest single operation in the engine (validation, destructive replace
with safety backup, one transaction, cache rebuild) and lived inline in a
mixin. It is a service with one collaborator set; the engine delegates.

Fail-closed contract, unchanged: a malformed snapshot raises before any
destructive step, a replace copies current state through SQLite's online
backup API first, and the derived-state rebuild happens once, after commit.
"""

from __future__ import annotations

from .types import Memory, MemoryType


class RestoreService:
    """Atomically apply a validated backup snapshot to a live engine."""

    def __init__(self, engine) -> None:
        # The engine is the unit of application: its db writes, its caches,
        # its derived-state scheduling. One owner per piece of state — the
        # service never mutates anything the engine does not expose.
        self._engine = engine

    async def restore(self, snapshot: dict, replace: bool = False) -> dict:
        """Atomically restore a fully validated backup snapshot.

        Validation is completed for every memory and session before a replace
        can delete current data.  The SQLite merge/replace is one transaction;
        caches and all derived graph/trust/conflict state are rebuilt only after
        commit.  Malformed snapshots fail closed with the existing store intact.
        """
        from .backup import BACKUP_FORMAT
        from .types import Session

        engine = self._engine

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
            existing_items = await engine.db.count_memories() + await engine.db.count_sessions()
            if existing_items:
                # Fail closed: a destructive replace is not allowed to proceed
                # unless the current SQLite state has first been copied with
                # SQLite's online-backup API. In-memory test stores have no
                # durable location and intentionally return None.
                safety_backup_path = await engine.db.create_safety_backup()

        await engine.db.restore_snapshot_transaction(
            [m.model_dump(mode="json") for m in memories],
            [s.model_dump(mode="json") for s in sessions],
            replace=replace,
            attachments=attachments,
        )

        # Rebuild process-local state from committed SQLite, never from the
        # untrusted snapshot objects.
        engine.short_term.clear()
        engine.vector_store.clear()
        # The mirror is rebuilt store-wide (#302), matching initialize/resync.
        restored_all = await engine.episodic.get_all(limit=1_000_000, across_workspaces=True)
        for memory in restored_all:
            if memory.memory_type == MemoryType.SHORT_TERM:
                engine.short_term.add(memory)
            if memory.embedding:
                engine.vector_store.add(memory)

        engine._derived_dirty = True
        # Freshness-required (issue #102): the caller's next read acts on
        # exactly this restored state.
        await engine.recompute_derived_state()

        engine._emit(
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

        from .attachment_store import attachments_dir

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
