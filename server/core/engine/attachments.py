"""Files attached to a memory as evidence — reference + derived text, not blob.

Part of :class:`server.core.memory_engine.MemoryEngine`, split out to keep
each file readable. Mixins rather than separate services: the methods use the
engine's own state throughout, and moving the bodies unchanged is what makes
the split verifiable.

The memory stays text, so decay/H(x,psi) keep working unmodified; the file
lives on disk and is referenced by path + sha256. ``verify_attachment`` is the
only place a moved/deleted/changed file becomes visible: it raises a
conflict candidate (self-referencing memory_id_a == memory_id_b) rather than
silently marking the memory wrong, which fits the same "candidate for human
review, never a verdict" contract as every other conflict signal in this
engine.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import uuid
from datetime import datetime, timezone


class MemoryAttachmentsMixin:
    """Attach local files to a memory as evidence, and keep them honest."""

    async def attach_file(
        self,
        memory_id: str,
        path: str,
        derived_text: str | None = None,
        derived_by: str = "manual",
    ) -> dict:
        """Attach a local file to an existing memory by reference.

        Raises ``ValueError`` if the memory doesn't exist or the path isn't a
        readable file. The file's bytes are hashed and sized once, at attach
        time, so a later :meth:`verify_attachment` has something to compare
        against.
        """
        memory = await self.get_memory(memory_id)
        if memory is None:
            raise ValueError(f"memory '{memory_id}' not found")

        resolved = os.path.abspath(os.path.expanduser(path))
        if not os.path.isfile(resolved):
            raise ValueError(f"'{path}' is not a readable file")

        sha256 = await self._sha256_file(resolved)
        size = os.path.getsize(resolved)
        mime, _ = mimetypes.guess_type(resolved)

        row = {
            "id": uuid.uuid4().hex,
            "memory_id": memory_id,
            "path": resolved,
            "sha256": sha256,
            "mime": mime,
            "size": size,
            "derived_text": derived_text,
            "derived_by": derived_by if derived_text else "none",
            "status": "ok",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "verified_at": None,
        }
        await self.db.insert_attachment(row)
        self._emit("attachment_added", {"memory_id": memory_id, "attachment_id": row["id"]})
        return row

    async def list_memory_attachments(self, memory_id: str) -> list[dict]:
        return await self.db.list_attachments(memory_id)

    async def get_attachment(self, attachment_id: str) -> dict | None:
        return await self.db.get_attachment(attachment_id)

    async def delete_attachment(self, attachment_id: str) -> bool:
        return await self.db.delete_attachment(attachment_id)

    async def verify_attachment(self, attachment_id: str) -> dict:
        """Re-check one attachment's file against what was recorded at attach
        time. A missing file or a hash mismatch raises a conflict candidate —
        the memory itself is left alone, exactly like every other conflict
        signal here: a candidate for human review, never an automatic verdict.
        """
        attachment = await self.db.get_attachment(attachment_id)
        if attachment is None:
            raise ValueError(f"attachment '{attachment_id}' not found")

        path = attachment["path"]
        if not os.path.isfile(path):
            await self.db.update_attachment_status(attachment_id, "missing")
            await self._raise_attachment_conflict(attachment, "missing", "file no longer exists at its recorded path")
            attachment["status"] = "missing"
            return attachment

        current_sha256 = await self._sha256_file(path)
        if current_sha256 != attachment["sha256"]:
            # The stored sha256 is deliberately NOT rebased to current_sha256
            # here: it stays the hash of what was actually attached, so if the
            # file is later restored to those exact bytes, verification goes
            # back to "ok" on its own instead of drifting to a new baseline
            # every time someone looks.
            await self.db.update_attachment_status(attachment_id, "changed")
            await self._raise_attachment_conflict(
                attachment, "changed", "file content no longer matches the hash recorded at attach time"
            )
            attachment["status"] = "changed"
            return attachment

        await self.db.update_attachment_status(attachment_id, "ok")
        # A file back in place/restored resolves any earlier candidate — still
        # an explicit, logged transition, not silent deletion.
        candidate_id = f"attachment:{attachment_id}"
        if await self.db.get_conflict(candidate_id):
            await self.db.update_conflict_status(
                candidate_id, "resolved", datetime.now(timezone.utc).isoformat()
            )
        attachment["status"] = "ok"
        return attachment

    async def verify_all_attachments(self) -> dict:
        """Verify every attachment. Returns counts by resulting status."""
        counts = {"ok": 0, "missing": 0, "changed": 0}
        for row in await self.db.all_attachments():
            result = await self.verify_attachment(row["id"])
            counts[result["status"]] = counts.get(result["status"], 0) + 1
        return counts

    async def _raise_attachment_conflict(self, attachment: dict, status: str, detail: str) -> None:
        """Self-referencing conflict candidate (memory_id_a == memory_id_b):
        the same deterministic table every conflict signal uses, just keyed to
        one memory instead of a pair, since a broken attachment is a fact
        about that memory alone."""
        memory_id = attachment["memory_id"]
        candidate_id = f"attachment:{attachment['id']}"
        explanation = {
            "signal_type": f"attachment_{status}",
            "detail": detail,
            "attachment_id": attachment["id"],
            "path": attachment["path"],
            "note": "Conflict CANDIDATE for human review — not a verdict.",
        }
        row = {
            "id": candidate_id,
            "memory_id_a": memory_id,
            "memory_id_b": memory_id,
            "shared_entities_json": "[]",
            "signal_type": f"attachment_{status}",
            "confidence": 1.0,
            "status": "open",
            "explanation_json": json.dumps(explanation),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if await self.db.insert_conflict_if_absent(row):
            self._emit("conflicts_detected", {"new": 1, "attachment_id": attachment["id"]})

    @staticmethod
    async def _sha256_file(path: str) -> str:
        hasher = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
