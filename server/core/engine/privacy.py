"""Deletion audits, hard purges and secret redaction.

Part of :class:`server.core.memory_engine.MemoryEngine`, split out to keep
each file readable. Mixins rather than separate services: the methods use the
engine's own state throughout, and moving the bodies unchanged is what makes
the split verifiable.
"""

from __future__ import annotations




class MemoryPrivacyMixin:
    """Deletion audits, hard purges and secret redaction."""

    async def audit_deletion(self, memory_id: str) -> dict:
        """Prove absence across runtime, primary and derived layers."""
        persistent = await self.db.memory_residue(memory_id)
        residue = {
            "short_term": self.short_term.find(memory_id) is not None,
            "vector_store": self.vector_store.get(memory_id) is not None,
            "episodic": persistent["episodic"] > 0,
            "entity_links": persistent["entity_links"] > 0,
            "trust_score": persistent["trust_score"] > 0,
            "conflict_candidates": persistent["conflict_candidates"] > 0,
        }
        return {
            "memory_id": memory_id,
            "residue": residue,
            "fully_absent": not any(residue.values()),
        }

    async def purge_memory(self, memory_id: str) -> dict:
        """Hard-delete a memory and verify nothing survives. ``forget`` already
        removes from every layer; this wraps it with a post-condition audit so
        a "really delete" is provable, not assumed. Pinned memories are deleted
        too — an explicit purge is a deliberate human action."""
        existed = (await self.episodic.get(memory_id)) is not None
        await self.forget(memory_id)
        audit = await self.audit_deletion(memory_id)
        return {
            "memory_id": memory_id,
            "existed": existed,
            "purged": audit["fully_absent"],
            "residue": audit["residue"],
        }

    async def audit_secrets(self, limit: int = 10000) -> dict:
        """Scan stored memories for secrets that slipped in before the
        admission gate existed (or via paths that bypass it). Read-only —
        reports which memories contain credentials so they can be redacted."""
        from ..admission import redact_secrets

        memories = await self.episodic.search(limit=limit)
        flagged = []
        for m in memories:
            redacted_content, secrets = redact_secrets(m.content or "")
            if secrets:
                flagged.append(
                    {
                        "id": m.id,
                        "secrets": secrets,
                        # Never echo the credential that the audit detected.
                        "preview": (redacted_content or "").split("\n", 1)[0][:100],
                        "project": m.project,
                        "source": m.source,
                    }
                )
        return {"scanned": len(memories), "flagged": len(flagged), "items": flagged}

    async def redact_memory(self, memory_id: str) -> dict:
        """Strip secrets from an already-stored memory in place: rewrite the
        content, re-embed, refresh every layer, and record the event in
        ``metadata.redaction_history``. Idempotent — a memory with no secrets
        is left untouched."""
        from datetime import datetime, timezone

        from ..admission import redact_secrets

        memory = await self.episodic.get(memory_id)
        if not memory:
            return {"ok": False, "error": "not found", "memory_id": memory_id}

        new_content, secrets = redact_secrets(memory.content or "")
        if not secrets:
            return {"ok": True, "redacted": False, "secrets": [], "memory_id": memory_id}

        memory.content = new_content
        memory.embedding = await self.embedder.embed(new_content)
        md = dict(memory.metadata or {})
        md["embedding_provenance"] = self.embedder.identity()
        history = list(md.get("redaction_history") or [])
        history.append(
            {"secrets": secrets, "at": datetime.now(timezone.utc).isoformat()}
        )
        md["redaction_history"] = history[-50:]
        memory.metadata = md

        await self.episodic.update(memory)
        self._refresh_memory_caches(memory)
        self.vector_store.add(memory)
        self._mark_derived_dirty()
        self._emit("updated", self._memory_event_payload(memory))
        return {
            "ok": True,
            "redacted": True,
            "secrets": secrets,
            "memory_id": memory_id,
        }

    async def redact_all_secrets(self, dry_run: bool = True, limit: int = 10000) -> dict:
        """Bulk redaction of secrets across stored memories. ``dry_run=True``
        (default) only reports; set False to rewrite every flagged memory."""
        audit = await self.audit_secrets(limit=limit)
        redacted = 0
        if not dry_run:
            for item in audit["items"]:
                result = await self.redact_memory(item["id"])
                if result.get("redacted"):
                    redacted += 1
        return {
            "dry_run": dry_run,
            "scanned": audit["scanned"],
            "flagged": audit["flagged"],
            "redacted": redacted,
            "items": audit["items"],
        }
