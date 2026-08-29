"""Sessions, projects, sources and tags.

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
    SessionStatus,
)


class MemorySessionsMixin:
    """Sessions, projects, sources and tags."""

    async def _refresh_session_count(self, session_id: str) -> None:
        count = await self.db.count_session_memories(session_id)
        await self.db.update_session(session_id, {"memory_count": count})

    async def create_session(
        self, name: str = "Untitled Session", metadata: dict | None = None
    ) -> Session:
        session = Session(name=name, metadata=metadata or {})
        await self.db.insert_session(session.model_dump())
        self._emit("session_created", session.model_dump())
        return session

    async def summarize_session(
        self, session_id: str, max_memories: int = 50, store: bool = True
    ) -> Memory | None:
        """Distill a session's memories into one consolidated summary memory.

        Uses an LLM when OPENAI_API_KEY is set, otherwise a deterministic
        extractive fallback — so it works fully offline. Returns the created
        summary Memory (or None when the session has no memories). When
        ``store`` is False the summary text is returned on a transient Memory
        without persisting.
        """
        from ..summarizer import summarize_texts

        memories = await self.episodic.search(session_id=session_id, limit=max_memories)
        texts = [m.content for m in memories if m.content]
        if not texts:
            return None

        client = self._embedder._http if self._embedder is not None else None
        summary_text = await summarize_texts(texts, mode="auto", client=client)
        if not summary_text.strip():
            return None

        header = f"Session summary ({len(texts)} memories):\n{summary_text}"
        if not store:
            return Memory(content=header, session_id=session_id, memory_type=MemoryType.EPISODIC)

        # Inherit the session's dominant project so the summary is namespaced.
        project = next((m.project for m in memories if m.project), None)
        summary = await self.store(
            content=header,
            importance=0.75,
            tags=["session-summary"],
            session_id=session_id,
            project=project,
            source="auto-summary",
            memory_type="episodic",
        )
        self._emit(
            "session_summarized",
            {"session_id": session_id, "summary_id": summary.id, "from_count": len(texts)},
        )
        return summary

    async def end_session(self, session_id: str) -> Session | None:
        """End a session: consolidate its short-term memories, optionally
        summarize them, then mark ended."""
        row = await self.db.get_session(session_id)
        if not row:
            return None

        consolidated = await self.consolidate(session_id=session_id)

        if self.auto_summarize:
            try:
                await self.summarize_session(session_id)
            except Exception:
                # Summarization is best-effort — never block session end on it.
                pass

        session = Session(**row)
        session.status = SessionStatus.ENDED
        session.ended_at = datetime.now(timezone.utc).isoformat()
        session.memory_count = await self.db.count_session_memories(session_id)
        await self.db.update_session(session_id, session.model_dump(exclude={"id"}))
        self._emit(
            "session_ended",
            {**session.model_dump(), "consolidated": consolidated},
        )
        return session

    async def list_sessions(self, limit: int = 50) -> list[Session]:
        rows = await self.db.get_all_sessions(limit)
        return [Session(**r) for r in rows]

    async def get_session(self, session_id: str) -> Session | None:
        row = await self.db.get_session(session_id)
        return Session(**row) if row else None

    async def delete_session(self, session_id: str, memories: str = "refuse") -> dict:
        """Delete a session, saying out loud what happens to its memories.

        A session that was created by mistake, or left empty, had no way out
        through the API: there was no DELETE, so removing one meant opening
        ``stackmemory.db`` and running SQL by hand. A product whose claim is
        auditability cannot have a write path that goes around its own API — a
        hand-edit leaves no trace anyone can read back.

        ``memories`` names the policy, and the default refuses rather than
        guesses:

          - ``refuse``  — delete only an empty session; otherwise report the
            count and change nothing. Deleting a session is a tidying action,
            and tidying must not quietly take memories with it.
          - ``detach``  — keep the memories, drop their session link.
          - ``delete``  — remove the memories too, through the same cascade a
            single delete uses, so entity/trust/conflict rows go with them.

        The policy is a required decision, not a flag with a convenient
        default: ``refuse`` is the only value that cannot lose anything.
        """
        if memories not in ("refuse", "detach", "delete"):
            return {"ok": False, "error": "invalid_memories_policy"}

        row = await self.db.get_session(session_id)
        if row is None:
            return {"ok": False, "error": "not_found"}

        memory_ids = await self.db.list_session_memory_ids(session_id)
        if memory_ids and memories == "refuse":
            return {
                "ok": False,
                "error": "session_not_empty",
                "memory_count": len(memory_ids),
            }

        detached = deleted_memories = 0
        if memories == "detach":
            detached = await self.db.detach_session_memories(session_id)
            # recall() scores from the vector store's cached Memory objects, so
            # a SQLite-only update would leave them carrying a session_id that
            # no longer resolves. They are refreshed, not evicted: a detached
            # memory must stay fully recallable — losing it from the cache
            # would be the same data loss by a slower route.
            for memory_id in memory_ids:
                memory = await self.episodic.get(memory_id)
                if memory is not None:
                    self._refresh_memory_caches(memory)
        elif memories == "delete":
            for memory_id in memory_ids:
                if await self.forget(memory_id):
                    deleted_memories += 1

        await self.db.delete_session(session_id)
        result = {
            "ok": True,
            "session_id": session_id,
            "memories_detached": detached,
            "memories_deleted": deleted_memories,
        }
        self._emit("session_deleted", result)
        return result

    async def list_projects(self) -> list[dict]:
        return await self.db.list_projects()

    async def list_sources(self) -> list[dict]:
        return await self.db.list_sources()

    async def list_tags(self) -> list[dict]:
        return await self.db.list_tags()
