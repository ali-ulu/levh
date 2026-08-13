"""The admission gate and connector ingest.

Part of :class:`server.core.memory_engine.MemoryEngine`, split out to keep
each file readable. Mixins rather than separate services: the methods use the
engine's own state throughout, and moving the bodies unchanged is what makes
the split verifiable.
"""

from __future__ import annotations


from ..types import (
    Memory,
)


class MemoryIngestMixin:
    """The admission gate and connector ingest."""

    async def evaluate_admission(
        self,
        content: str,
        project: str | None = None,
        min_length: int = 3,
        exclude_id: str | None = None,
    ) -> dict:
        """Judge a candidate memory WITHOUT storing it: admit / review / redact
        / reject. Computes the duplicate signal (max cosine similarity to any
        existing memory) from the vector store, then applies the deterministic
        admission rules. No LLM.

        The duplicate probe embeds the *redacted* text — i.e. what would
        actually be stored — so that re-ingesting the same secret-bearing item
        (common when a connector re-syncs) is correctly seen as a duplicate
        instead of accumulating near-identical copies."""
        from ..admission import evaluate, redact_secrets

        probe_text, _ = redact_secrets(content or "")
        probe_text = (probe_text or "").strip()
        max_sim = 0.0
        if probe_text:
            embedding = await self.embedder.embed(probe_text)

            def _pred(m: Memory) -> bool:
                # A memory being updated is its own nearest neighbour, so
                # without this it would always look like a duplicate of itself.
                if exclude_id is not None and m.id == exclude_id:
                    return False
                return project is None or m.project == project

            neighbours = self.vector_store.search(embedding, top_k=1, predicate=_pred)
            if neighbours:
                max_sim = float(neighbours[0][1])
        return evaluate(content, max_similarity=max_sim, min_length=min_length)

    async def admit_memory(
        self,
        content: str,
        importance: float = 0.5,
        tags: list[str] | None = None,
        session_id: str | None = None,
        project: str | None = None,
        source: str | None = None,
        pinned: bool = False,
        memory_type: str = "short_term",
        metadata: dict | None = None,
        force: bool = False,
        min_length: int = 3,
    ) -> dict:
        """Run the admission gate, then act on its verdict:

          - ``reject`` / ``review`` → NOT stored (unless ``force=True``);
          - ``redact``              → stored with secrets stripped;
          - ``admit``               → stored as-is.

        The verdict is recorded in the stored memory's ``metadata.admission``.
        Returns ``{"stored": bool, "decision": <gate result>, "memory": <dict|None>}``.
        """
        decision = await self.evaluate_admission(
            content, project=project, min_length=min_length
        )
        action = decision["action"]

        if action in ("reject", "review") and not force:
            return {"stored": False, "decision": decision, "memory": None}

        store_content = (
            decision["redacted_content"] if decision["redacted"] else content
        )
        md = dict(metadata or {})
        md["admission"] = {
            "action": action,
            "reasons": decision["reasons"],
            "reason_codes": decision["reason_codes"],
            "redacted": decision["redacted"],
            "secrets": decision["secrets"],
            "max_similarity": decision["max_similarity"],
            "forced": bool(force and action in ("reject", "review")),
        }
        mem = await self.store(
            content=store_content,
            importance=importance,
            tags=tags,
            session_id=session_id,
            memory_type=memory_type,
            metadata=md,
            project=project,
            source=source,
            pinned=pinned,
        )
        return {"stored": True, "decision": decision, "memory": mem.model_dump(exclude={"embedding"})}

    async def ingest_items(
        self,
        items: list[dict],
        connector: str,
        project: str | None = None,
        use_gate: bool = True,
    ) -> dict:
        """Store a batch of connector-fetched items, routed through the
        admission gate (dedupe + secret redaction) when ``use_gate`` is True.

        This is Connector-framework-v2 ingest: every item is isolated (one bad
        item can't fail the batch), duplicates are dropped instead of piling
        up, secrets are stripped, and the run is recorded in ``connector_sync``
        so re-syncing is incremental and reportable ("N new since last sync").

        Returns a breakdown:
            fetched, stored, redacted, duplicates, held, errors, source_key,
            last_synced_at.
        """
        from datetime import datetime, timezone

        source = f"connector:{connector}"
        stored = redacted = duplicates = held = errors = 0

        for item in items:
            content = (item or {}).get("content", "")
            if not content or not str(content).strip():
                continue
            tags = item.get("tags", []) or []
            metadata = dict(item.get("metadata", {}) or {})
            metadata["imported_via"] = connector
            importance = float(item.get("importance", 0.5))
            try:
                if use_gate:
                    result = await self.admit_memory(
                        content=content,
                        importance=importance,
                        tags=tags,
                        project=project,
                        source=source,
                        memory_type="episodic",
                        metadata=metadata,
                    )
                    action = result["decision"]["action"]
                    if not result["stored"]:
                        # reject (duplicate) vs review (held for a human)
                        if action == "review":
                            held += 1
                        else:
                            duplicates += 1
                        continue
                    stored += 1
                    if action == "redact":
                        redacted += 1
                else:
                    await self.store(
                        content=content,
                        importance=importance,
                        tags=tags,
                        project=project,
                        source=source,
                        memory_type="episodic",
                        metadata=metadata,
                    )
                    stored += 1
            except Exception:
                # Error isolation — a single malformed item never fails the run.
                errors += 1
                continue

        now_iso = datetime.now(timezone.utc).isoformat()
        source_key = f"{connector}:{project or ''}"
        await self.db.record_sync(
            source_key=source_key,
            connector=connector,
            project=project,
            last_synced_at=now_iso,
            fetched=len(items),
            stored=stored,
        )
        self._emit(
            "connector_synced",
            {"connector": connector, "stored": stored, "duplicates": duplicates},
        )
        return {
            "connector": connector,
            "fetched": len(items),
            "stored": stored,
            "redacted": redacted,
            "duplicates": duplicates,
            "held": held,
            "errors": errors,
            "source_key": source_key,
            "last_synced_at": now_iso,
        }

    async def list_sync_state(self) -> list[dict]:
        """All connector sync bookkeeping rows, most-recent first."""
        return await self.db.list_sync_states()
