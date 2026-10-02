"""Re-embed stored memories after the embedder changes.

Part of :class:`server.core.memory_engine.MemoryEngine`, split out for
readability like the other mixins.

Switching ``EMBEDDER_MODE`` (``hash`` → ``local``/``openai``/``ollama``, or
back) leaves every stored vector in the *old* embedder's space. Recall compares
only vectors whose dimension matches the query and silently skips the rest, so
after such a switch the pre-existing memories simply stop being reachable —
the "it forgets everything" symptom, again, from a different cause. ``levh
doctor`` already warns ("re-embed before relying on complete recall"); this
mixin is the re-embed it was asking for.

Vectors are re-derived from each memory's *content*, which is the only input
the embedder ever saw, so no source is needed beyond the store itself.
``metadata.embedding_provenance`` (written on every store/update) names the
embedder that produced the vector currently held, which is how a memory is
judged stale: its provenance differs from the resolved embedder's identity.
"""

from __future__ import annotations

from typing import Callable, Optional

from ..types import Memory
from ..tenancy import Principal, bind_principal, reset_principal

# Re-embedding is network/model-bound, so the unit of work is one memory; the
# batch size only decides how often progress is reported and the event loop is
# yielded, not how many texts go to the embedder at once.
_PROGRESS_EVERY = 25


class MemoryReembedMixin:
    """Re-derive stored vectors from content under the active embedder."""

    async def reembed_memories(
        self,
        project: Optional[str] = None,
        dry_run: bool = False,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> dict:
        """Re-embed memories whose vector does not match the active embedder.

        Args:
            project: Restrict to one project; ``None`` means every memory.
            dry_run: Report what would change and touch nothing.
            on_progress: Called as ``(done, total)`` after each memory, so a
                long run can show a progress line.

        Returns a summary: the resolved embedder identity, how many memories
        were examined and how many were/would be re-embedded, and a per-project
        breakdown of the stale set (the diagnostic value is seeing *which*
        workspace holds the unreachable memories).
        """
        identity = self.embedder.identity()
        # Maintenance pass over the whole store, not just the current workspace
        # (#302): a re-embed is not a read any user is scoped to, and skipping
        # other workspaces would leave their vectors stale forever.
        memories = await self.episodic.get_all(across_workspaces=True)
        if project is not None:
            memories = [m for m in memories if m.project == project]

        stale = [
            m for m in memories if not m.embedding or self._provenance_of(m) != identity
        ]
        summary = {
            "provider": identity.get("provider"),
            "model": identity.get("model"),
            "dimension": identity.get("dimension"),
            "scanned": len(memories),
            "stale": len(stale),
            "reembedded": 0,
            "dry_run": dry_run,
            "by_project": self._stale_by_project(stale),
        }
        if dry_run or not stale:
            return summary

        done = 0
        for memory in stale:
            await self._reembed_one(memory, identity)
            done += 1
            if on_progress is not None:
                on_progress(done, len(stale))
            elif done % _PROGRESS_EVERY == 0:
                await self._yield_for_progress()
        summary["reembedded"] = done
        return summary

    @staticmethod
    def _provenance_of(memory: Memory) -> Optional[dict]:
        return (memory.metadata or {}).get("embedding_provenance")

    @staticmethod
    def _stale_by_project(stale: list[Memory]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for memory in stale:
            key = memory.project or "(none)"
            counts[key] = counts.get(key, 0) + 1
        return counts

    async def _reembed_one(self, memory: Memory, identity: dict) -> None:
        """Re-embed one memory and refresh the DB row and the vector store.

        The vector store is the live read path, so it must be updated in the
        same step as the row or a running server would keep serving the old
        vector until its next reload. The two calls mirror ``update_memory``/
        the write path so the stored shape cannot drift from a normal store.

        The scan spans every workspace (#302) while ``episodic.update`` is
        scoped to the current one, so the memory's own workspace is bound for
        the write and restored afterwards — including when the embedder raises,
        or a later call would run in the wrong workspace.
        """
        memory.embedding = await self.embedder.embed(memory.content)
        metadata = dict(memory.metadata or {})
        metadata["embedding_provenance"] = identity
        memory.metadata = metadata
        token = bind_principal(Principal(workspace_id=memory.workspace_id))
        try:
            await self.episodic.update(memory)
        finally:
            reset_principal(token)
        self.vector_store.add(memory)  # keyed by id → replaces the stale copy

    @staticmethod
    async def _yield_for_progress() -> None:
        import asyncio

        await asyncio.sleep(0)
