"""Demo data seeding and onboarding state.

Part of :class:`server.core.memory_engine.MemoryEngine`, split out to keep
each file readable. Mixins rather than separate services: the methods use the
engine's own state throughout, and moving the bodies unchanged is what makes
the split verifiable.
"""

from __future__ import annotations




class MemoryDemoMixin:
    """Demo data seeding and onboarding state."""

    async def seed_demo(self, force: bool = False) -> dict:
        """Populate an empty store with a deterministic demo corpus so a first
        run shows a live dashboard instead of empty states.

        Every memory flows through the real ``store`` path, is backdated from
        its ``age_days`` (so the forgetting curve, review queue, and briefing
        show a genuine time spread), and — where marked — has its durability
        nudged to model strong vs fading memories. After ingest the full
        derived pipeline runs (entity reindex → trust recompute → conflict
        detection), so people, organizations, the trust breakdown, and one real
        conflict candidate are all present immediately.

        Refuses to run on a non-empty store unless ``force=True`` — it never
        touches memories the user already has.
        """
        from datetime import datetime, timedelta, timezone

        from ..demo_data import demo_memories

        existing = await self.episodic.count()
        if existing and not force:
            return {
                "seeded": 0,
                "skipped": True,
                "reason": "store not empty",
                "existing": existing,
            }

        now = datetime.now(timezone.utc)
        seeded_ids: list[str] = []
        for item in demo_memories():
            age_days = int(item.get("age_days", 0))
            created = now - timedelta(days=age_days)
            created_iso = created.isoformat()

            metadata = dict(item.get("metadata") or {})
            # Anchor event-like memories in time so meeting-prep / timeline order
            # them by when they happened, matching how real connectors capture.
            metadata.setdefault("captured_at", created_iso)
            metadata.setdefault("demo", True)

            mem = await self.store(
                content=item["content"],
                importance=float(item.get("importance", 0.5)),
                tags=list(item.get("tags") or []),
                memory_type="episodic",
                metadata=metadata,
                project=item.get("project"),
                source=item.get("source"),
                pinned=bool(item.get("pinned", False)),
            )

            # Backdate the record. A reinforced memory keeps a recent
            # accessed_at (it's been recalled lately, so it stays strong);
            # everything else was last touched when it was created, so it decays.
            reinforce = item.get("reinforce") or {}
            updates: dict = {
                "created_at": created_iso,
                "frequency": int(reinforce.get("frequency", mem.frequency)),
                "recall_count": int(reinforce.get("recall_count", mem.recall_count)),
                "stability_hours": float(
                    reinforce.get("stability_hours", mem.stability_hours)
                ),
            }
            if reinforce.get("recall_count"):
                # Strong memory: recalled within the last day.
                updates["accessed_at"] = (now - timedelta(hours=6)).isoformat()
            else:
                updates["accessed_at"] = created_iso

            # Keep the in-memory object (shared by the vector store) in sync so
            # scoring in this same process sees the backdated values.
            mem.created_at = updates["created_at"]
            mem.accessed_at = updates["accessed_at"]
            mem.frequency = updates["frequency"]
            mem.recall_count = updates["recall_count"]
            mem.stability_hours = updates["stability_hours"]
            await self.db.update_memory(mem.id, updates)
            seeded_ids.append(mem.id)

        # Build everything the dashboard reads off derived tables.
        entities = await self.reindex_entities()
        trust = await self.recompute_trust_scores()
        conflicts = await self.detect_conflict_candidates()

        self._emit("demo_seeded", {"seeded": len(seeded_ids)})
        return {
            "seeded": len(seeded_ids),
            "skipped": False,
            "entities": entities.get("entities", 0),
            "entity_links": entities.get("links", 0),
            "trust_scored": trust.get("scored", 0),
            "conflict_candidates": conflicts.get("new_candidates", 0),
        }

    async def onboarding_status(self) -> dict:
        """Return deterministic first-run readiness computed from real state."""
        from ..onboarding import onboarding_status

        return await onboarding_status(self)

    async def remove_demo_data(self) -> dict:
        """Remove only memories explicitly marked as demo data."""
        from ..onboarding import remove_demo_data

        return await remove_demo_data(self)
