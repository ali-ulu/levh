"""Decay, reinforcement and the spaced-repetition review queue.

Part of :class:`server.core.memory_engine.MemoryEngine`, split out to keep
each file readable. Mixins rather than separate services: the methods use the
engine's own state throughout, and moving the bodies unchanged is what makes
the split verifiable.
"""

from __future__ import annotations


from ..types import (
    Memory,
)


class MemoryDecayMixin:
    """Decay, reinforcement and the spaced-repetition review queue."""

    async def reinforce_memory(self, memory_id: str) -> Memory | None:
        """Manually strengthen a memory without a full recall — the explicit
        version of what happens automatically when a memory is recalled.
        Resets its decay clock and grows its stability (half-life)."""
        memory = await self.episodic.get(memory_id)
        if not memory:
            return None
        memory.stability_hours = self.scorer.reinforce(memory.stability_hours, memory.importance)
        memory.recall_count += 1
        memory.touch()
        await self.episodic.update(memory)
        self._refresh_memory_caches(memory)
        self._mark_derived_dirty()
        self._emit("updated", self._memory_event_payload(memory))
        return memory

    async def memory_feedback(self, memory_id: str, helpful: bool) -> Memory | None:
        """Learn from recall outcomes, SM-2 style.

        helpful=True  → the memory was correct/useful: reinforce it (resets
                        the decay clock, grows stability).
        helpful=False → the memory was wrong or stale: weaken its stability
                        WITHOUT resetting the clock, so it fades out quickly
                        unless someone rescues it (edit, pin, or reinforce).
        """
        memory = await self.episodic.get(memory_id)
        if not memory:
            return None
        if helpful:
            memory.stability_hours = self.scorer.reinforce(
                memory.stability_hours, memory.importance
            )
            memory.recall_count += 1
            memory.touch()
        else:
            memory.stability_hours = self.scorer.weaken(memory.stability_hours)
        await self.episodic.update(memory)
        self._refresh_memory_caches(memory)
        self._mark_derived_dirty()
        self._emit("updated", self._memory_event_payload(memory))
        return memory

    async def list_fading(
        self,
        threshold: float = 0.35,
        project: str | None = None,
        limit: int = 20,
    ) -> list[tuple[Memory, float]]:
        """Memories whose predicted retention has dropped below `threshold` —
        the 'about to be forgotten' review queue. Pinned memories never fade.
        Returns (memory, retention) pairs, most-faded first."""
        memories = await self.episodic.search(project=project, limit=2000)
        fading: list[tuple[Memory, float]] = []
        for m in memories:
            if m.pinned:
                continue
            retention = self.scorer.compute_decay(
                m.accessed_at, half_life_hours=m.stability_hours
            )
            if retention < threshold:
                fading.append((m, retention))
        fading.sort(key=lambda pair: pair[1])
        return fading[:limit]

    async def review_queue(
        self, threshold: float = 0.5, project: str | None = None, limit: int = 20
    ) -> list[dict]:
        """Memories due for a spaced-repetition review: fading (retention below
        ``threshold``), not pinned, and not currently snoozed. Each item carries
        the decay context a human needs to decide keep / reinforce / weaken /
        pin / forget / snooze. No LLM.

        Turns the passive fading queue into an active review flow — the last
        step of the lifecycle: store → recall → decay → **review** →
        reinforce/weaken/forget.
        """
        from datetime import datetime, timezone

        now_iso = datetime.now(timezone.utc).isoformat()
        # Pull a wider fading set, then drop snoozed ones and cap to `limit`.
        fading = await self.list_fading(
            threshold=threshold, project=project, limit=max(limit * 3, limit)
        )

        items: list[dict] = []
        for m, retention in fading:
            review = (m.metadata or {}).get("review") or {}
            due_at = review.get("review_due_at")
            if due_at and due_at > now_iso:
                continue  # snoozed — not due yet
            items.append(
                {
                    "id": m.id,
                    "content": m.content.split("\n", 1)[0][:160],
                    "project": m.project,
                    "source": m.source,
                    "importance": m.importance,
                    "hscore": m.hscore,
                    "retention": round(retention, 4),
                    "stability_hours": m.stability_hours,
                    "last_accessed": m.accessed_at,
                    "recall_count": m.recall_count,
                    "review_count": int(review.get("review_count", 0)),
                    "reason": (
                        f"retention {round(retention, 2)} is below {threshold} "
                        f"(stability {round(m.stability_hours, 1)}h, "
                        f"{m.recall_count} recalls)"
                    ),
                }
            )
            if len(items) >= limit:
                break
        return items

    async def apply_review(
        self,
        memory_id: str,
        action: str,
        snooze_days: int = 7,
        reason: str = "",
    ) -> dict:
        """Apply a review decision to a memory and record it auditable-y in
        ``metadata.review`` + ``metadata.review_history``. Actions:

          - ``keep``      — reset the decay clock (mild reinforcement), no
                            stability change; the memory stays active.
          - ``reinforce`` — strong stability increase (full reinforcement).
          - ``weaken``    — decrease stability so it fades faster.
          - ``pin``       — pin it (never decays, never auto-forgotten).
          - ``forget``    — remove via the existing forget path.
          - ``snooze``    — push the next review out by ``snooze_days`` without
                            changing decay.

        Pinned memories are never auto-forgotten; ``forget`` here is an explicit
        human decision, so it is honoured. Returns a small result dict.
        """
        from datetime import datetime, timedelta, timezone

        valid = {"keep", "reinforce", "weaken", "forget", "pin", "snooze"}
        if action not in valid:
            raise ValueError(
                f"invalid review action '{action}'; expected one of {sorted(valid)}"
            )

        memory = await self.episodic.get(memory_id)
        if not memory:
            return {"ok": False, "error": "not found", "memory_id": memory_id}

        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        if action == "forget":
            removed = await self.forget(memory_id)
            return {
                "ok": removed,
                "action": "forget",
                "memory_id": memory_id,
                "removed": removed,
            }

        # Decay-affecting actions delegate to the existing tested paths, then we
        # re-fetch so the review metadata is written on top of their changes.
        if action == "reinforce":
            await self.reinforce_memory(memory_id)
            memory = await self.episodic.get(memory_id) or memory
        elif action == "weaken":
            await self.memory_feedback(memory_id, helpful=False)
            memory = await self.episodic.get(memory_id) or memory
        elif action == "pin":
            memory.pinned = True
        elif action == "keep":
            memory.touch()  # reset decay clock only — mild, no stability change

        md = dict(memory.metadata or {})
        review = dict(md.get("review") or {})
        review["last_reviewed_at"] = now_iso
        review["review_count"] = int(review.get("review_count", 0)) + 1
        review["last_action"] = action
        if action == "snooze":
            review["review_due_at"] = (
                now + timedelta(days=max(snooze_days, 1))
            ).isoformat()
        else:
            review.pop("review_due_at", None)  # no longer snoozed
        md["review"] = review

        history = list(md.get("review_history") or [])
        history.append({"action": action, "reason": reason, "at": now_iso})
        md["review_history"] = history[-50:]  # keep the last 50 decisions
        memory.metadata = md

        await self.episodic.update(memory)
        self._refresh_memory_caches(memory)
        self._mark_derived_dirty()
        self._emit("reviewed", {"memory_id": memory_id, "action": action})
        return {
            "ok": True,
            "action": action,
            "memory_id": memory_id,
            "review_count": review["review_count"],
            "review_due_at": review.get("review_due_at"),
            "pinned": memory.pinned,
        }

    async def get_forgetting_curve(self, memory_id: str, days: int = 30) -> dict | None:
        """Predicted retention curve for a memory over the next `days`,
        given its current stability — visualizes the Ebbinghaus-style
        forgetting curve this memory is following right now."""
        memory = await self.episodic.get(memory_id)
        if not memory:
            return None
        current_decay = (
            1.0
            if memory.pinned
            else self.scorer.compute_decay(memory.accessed_at, half_life_hours=memory.stability_hours)
        )
        return {
            "memory_id": memory_id,
            "pinned": memory.pinned,
            "stability_hours": memory.stability_hours,
            "recall_count": memory.recall_count,
            "current_retention": current_decay,
            "curve": self.scorer.retention_curve(memory.stability_hours, days=days),
        }
