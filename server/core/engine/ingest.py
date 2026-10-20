"""The admission gate and connector ingest.

Part of :class:`server.core.memory_engine.MemoryEngine`, split out to keep
each file readable. Mixins rather than separate services: the methods use the
engine's own state throughout, and moving the bodies unchanged is what makes
the split verifiable.
"""

from __future__ import annotations

import hashlib
import json
import time

from .. import metrics
from ..tenancy import authorize, current_workspace_id
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
        # The duplicate signal is only as trustworthy as the embedder behind it.
        # With the position-based hash fallback the cosine reflects character
        # positions, not meaning — distinct facts with a shared prefix score as
        # "possible duplicate" and would be held for review, i.e. lost, which is
        # the one failure the gate exists to prevent. Under that mode the gate
        # narrows to exact-duplicate detection (which recall-side lexical
        # matching does not need either). See Embedder.is_semantic.
        if probe_text and self.embedder.is_semantic:
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
        elif probe_text:
            # Model-free mode: a byte-for-byte identical memory is still a
            # duplicate. Recall can tell "same words" from "same meaning" via
            # word overlap; the gate only needs to stop literal re-stores.
            max_sim = 1.0 if await self._has_identical_content(probe_text, project) else 0.0
        return evaluate(content, max_similarity=max_sim, min_length=min_length)

    async def _has_identical_content(self, content: str, project: str | None) -> bool:
        return await self.db.memories.content_exists(content, project=project)

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

          - ``reject``              → NOT stored, and not kept;
          - ``review``              → NOT stored, but HELD for a human;
          - ``redact``              → stored with secrets stripped;
          - ``admit``               → stored as-is.

        The verdict is recorded in the stored memory's ``metadata.admission``.
        Returns ``{"stored": bool, "decision": <gate result>, "memory":
        <dict|None>, "held_id": <str|None>}``.

        ``review`` and ``reject`` are not the same refusal and must not share a
        path. ``reject`` is the gate deciding — too short, or a near-exact
        duplicate of something already remembered, so nothing is lost by
        dropping it. ``review`` is the gate declining to decide: the candidate
        is close to an existing memory but not identical, which is exactly the
        case where the difference may be the part worth keeping. Discarding it
        would throw away content on the strength of a judgement the gate itself
        refused to make.
        """
        authorize("admit", current_workspace_id())
        decision = await self.evaluate_admission(
            content, project=project, min_length=min_length
        )
        action = decision["action"]
        metrics.inc("levh_admission_verdict_total", decision=action)

        if action == "review" and not force:
            held_id = await self.hold_for_review(
                content=content,
                decision=decision,
                importance=importance,
                tags=tags,
                session_id=session_id,
                project=project,
                source=source,
                pinned=pinned,
                memory_type=memory_type,
                metadata=metadata,
            )
            return {"stored": False, "decision": decision, "memory": None, "held_id": held_id}

        if action == "reject" and not force:
            return {"stored": False, "decision": decision, "memory": None, "held_id": None}

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
        return {
            "stored": True,
            "decision": decision,
            "memory": mem.model_dump(exclude={"embedding"}),
            "held_id": None,
        }

    async def hold_for_review(
        self,
        content: str,
        decision: dict,
        importance: float = 0.5,
        tags: list[str] | None = None,
        session_id: str | None = None,
        project: str | None = None,
        source: str | None = None,
        pinned: bool = False,
        memory_type: str = "short_term",
        metadata: dict | None = None,
    ) -> str:
        """Park a ``review`` candidate where a human can find it, and return its
        id.

        The candidate is stored verbatim, with everything needed to admit it
        later unchanged -- importance, tags, session, project, source, pinned,
        type. Admitting it must produce the memory the caller originally asked
        for, not a reconstruction of it.

        Secrets are the one exception: the redacted text is what gets held. The
        gate has already found them, and a queue a person reads later is no
        place to keep a live credential.
        """
        import json
        import uuid
        from datetime import datetime, timezone

        held_id = uuid.uuid4().hex
        await self.db.insert_held_memory(
            {
                "id": held_id,
                "content": decision["redacted_content"] if decision["redacted"] else content,
                "importance": max(0.0, min(1.0, importance)),
                "tags_json": json.dumps(tags or []),
                "session_id": session_id,
                "project": project,
                "source": source,
                "memory_type": memory_type,
                "pinned": int(bool(pinned)),
                "metadata_json": json.dumps(metadata or {}),
                "reasons_json": json.dumps(decision.get("reasons") or []),
                "max_similarity": float(decision.get("max_similarity") or 0.0),
                "status": "held",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "decided_at": None,
                "admitted_memory_id": None,
            }
        )
        return held_id

    async def admit_held_memory(self, held_id: str) -> dict:
        """A human's decision to keep a held candidate.

        Stores it with ``force=True``: the gate already judged this content and
        returned ``review``, so re-running it would return ``review`` again and
        hold the candidate a second time. The decision being recorded here is
        the human's, and it overrides the gate by design -- ``metadata.admission
        .forced`` already records that it was overridden.
        """
        from datetime import datetime, timezone

        held = await self.db.get_held_memory(held_id)
        if held is None:
            return {"ok": False, "error": "not_found"}
        if held["status"] != "held":
            return {"ok": False, "error": "already_decided", "status": held["status"]}

        import json

        result = await self.admit_memory(
            content=held["content"],
            importance=float(held["importance"]),
            tags=json.loads(held["tags_json"] or "[]"),
            session_id=held["session_id"],
            project=held["project"],
            source=held["source"],
            pinned=bool(held["pinned"]),
            memory_type=held["memory_type"],
            metadata=json.loads(held["metadata_json"] or "{}"),
            force=True,
        )
        memory_id = (result.get("memory") or {}).get("id")
        # The row is closed only after the memory exists. If the store above
        # raises, the candidate stays 'held' and can be retried -- losing it
        # here would reintroduce exactly the bug this table was added to fix.
        claimed = await self.db.mark_held_memory_decided(
            held_id, "admitted", datetime.now(timezone.utc).isoformat(), memory_id
        )
        if not claimed:
            return {"ok": False, "error": "already_decided"}
        return {"ok": True, "memory": result.get("memory"), "held_id": held_id}

    async def discard_held_memory(self, held_id: str) -> dict:
        """A human's decision to drop a held candidate. The row stays, with its
        verdict, so the queue is an auditable record of what was decided rather
        than only of what is still waiting."""
        from datetime import datetime, timezone

        held = await self.db.get_held_memory(held_id)
        if held is None:
            return {"ok": False, "error": "not_found"}
        claimed = await self.db.mark_held_memory_decided(
            held_id, "discarded", datetime.now(timezone.utc).isoformat(), None
        )
        if not claimed:
            return {"ok": False, "error": "already_decided", "status": held["status"]}
        return {"ok": True, "held_id": held_id}

    async def _connector_source_versions(
        self, source: str, source_identity: str
    ) -> list[Memory]:
        """Return every stored version of one connector object.

        The source column narrows the query to the connector; metadata carries
        the connector-neutral external identity. Read old versions too, because
        they are the reconciliation lineage and repeated source revisions must
        not create duplicates after an intervening revision.
        """
        versions: list[Memory] = []
        offset = 0
        page_size = 500
        while True:
            batch = await self.episodic.search(
                source=source,
                include_superseded=True,
                limit=page_size,
                offset=offset,
                fetch_embeddings=True,
            )
            versions.extend(
                memory
                for memory in batch
                if (memory.metadata or {}).get("source_identity") == source_identity
            )
            if len(batch) < page_size:
                break
            offset += len(batch)
        return versions

    async def _ingest_connector_source_version(
        self,
        item: dict,
        connector: str,
        project: str | None,
        use_gate: bool,
    ) -> dict:
        """Idempotently ingest one identified external source revision.

        A stable source identity is authoritative for version matching; fuzzy
        dedupe is not allowed to discard a changed upstream record. The normal
        admission path still redacts secrets. Each changed snapshot becomes a
        new memory version, and the previous current version stays queryable
        through the temporal-history APIs.
        """
        from ..admission import redact_secrets
        from .write import _retirement_instant

        content = str((item or {}).get("content", "") or "")
        metadata = dict((item or {}).get("metadata", {}) or {})
        source_identity = str(metadata.get("source_identity", "") or "").strip()
        if not source_identity:
            return {"handled": False}
        if len(content.strip()) < 3:
            return {"handled": True, "rejected": True, "errors": 1}

        source = f"connector:{connector}"
        metadata["imported_via"] = connector
        fingerprint_content = redact_secrets(content)[0] if use_gate else content

        # Do not hash engine-generated metadata. Hash the source snapshot,
        # including upstream revision/state/labels, so state-only changes are
        # detectable even when the rendered text is identical.
        engine_metadata = {
            "imported_via",
            "admission",
            "embedding_provenance",
            "source_snapshot_sha256",
            "source_superseded_at",
            "source_superseded_by",
        }
        source_metadata = {
            key: value
            for key, value in metadata.items()
            if key not in engine_metadata
        }
        upstream_revision = str(source_metadata.get("source_revision", "") or "").strip()
        snapshot_payload = json.dumps(
            {"content": fingerprint_content, "metadata": source_metadata},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        snapshot_hash = hashlib.sha256(snapshot_payload.encode("utf-8")).hexdigest()
        revision = upstream_revision or snapshot_hash
        metadata["source_revision"] = revision
        metadata["source_snapshot_sha256"] = snapshot_hash

        versions = await self._connector_source_versions(source, source_identity)
        current_versions = [memory for memory in versions if memory.valid_to is None]
        matching_current = [
            memory
            for memory in current_versions
            if (memory.metadata or {}).get("source_snapshot_sha256") == snapshot_hash
        ]

        if matching_current:
            # In case a prior interrupted run left more than one current row,
            # converge on the newest identical snapshot without creating another.
            canonical = max(matching_current, key=lambda memory: memory.created_at)
            retirement_errors = 0
            for previous in current_versions:
                if previous.id == canonical.id:
                    continue
                try:
                    valid_to = _retirement_instant(previous.valid_from)
                    won = await self.db.retire_if_current(
                        previous.id, valid_to, canonical.id
                    )
                    if won:
                        previous.valid_to = valid_to
                        previous.superseded_by = canonical.id
                        self._refresh_memory_caches(previous)
                    else:
                        fresh = await self.episodic.get(previous.id)
                        if fresh is not None:
                            self._refresh_memory_caches(fresh)
                            if fresh.valid_to is None:
                                retirement_errors += 1
                except Exception:  # noqa: BLE001 - preserve the source item; report repair failure
                    retirement_errors += 1
            return {
                "handled": True,
                "stored": False,
                "duplicate": True,
                "redacted": False,
                "errors": retirement_errors,
            }

        tags = (item or {}).get("tags", []) or []
        importance = float((item or {}).get("importance", 0.5))
        if use_gate:
            result = await self.admit_memory(
                content=content,
                importance=importance,
                tags=tags,
                project=project,
                source=source,
                memory_type="episodic",
                metadata=metadata,
                force=True,
            )
            if not result["stored"]:
                return {"handled": True, "rejected": True, "errors": 1}
            stored_memory = result.get("memory") or {}
            was_redacted = bool(result["decision"].get("redacted"))
        else:
            stored_memory = (
                await self.store(
                    content=content,
                    importance=importance,
                    tags=tags,
                    project=project,
                    source=source,
                    memory_type="episodic",
                    metadata=metadata,
                )
            ).model_dump(exclude={"embedding"})
            was_redacted = False

        new_id = stored_memory.get("id")
        if not new_id:
            return {"handled": True, "rejected": True, "errors": 1}

        retirement_errors = 0
        for previous in current_versions:
            try:
                valid_to = _retirement_instant(previous.valid_from)
                won = await self.db.retire_if_current(previous.id, valid_to, new_id)
                if won:
                    previous.valid_to = valid_to
                    previous.superseded_by = new_id
                    self._refresh_memory_caches(previous)
                else:
                    fresh = await self.episodic.get(previous.id)
                    if fresh is not None:
                        self._refresh_memory_caches(fresh)
                        if fresh.valid_to is None:
                            retirement_errors += 1
            except Exception:  # noqa: BLE001 - the new snapshot stays stored and can be reconciled on retry
                retirement_errors += 1

        return {
            "handled": True,
            "stored": True,
            "redacted": was_redacted,
            "errors": retirement_errors,
        }

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
            last_synced_at, plus ``timing_ms`` (items loop, sync bookkeeping,
            and method total in milliseconds). The timing is diagnostic: it
            tells a slow sync apart by stage instead of by guessing.
        """
        authorize("admit", current_workspace_id())
        from datetime import datetime, timezone

        total_start = time.perf_counter()
        source = f"connector:{connector}"
        stored = redacted = duplicates = held = errors = 0
        items_ms = 0.0

        for item in items:
            content = (item or {}).get("content", "")
            if not content or not str(content).strip():
                continue
            tags = item.get("tags", []) or []
            metadata = dict(item.get("metadata", {}) or {})
            metadata["imported_via"] = connector
            importance = float(item.get("importance", 0.5))
            item_start = time.perf_counter()
            try:
                if metadata.get("source_identity"):
                    source_result = await self._ingest_connector_source_version(
                        item, connector, project, use_gate
                    )
                    if source_result.get("handled"):
                        source_errors = int(source_result.get("errors", 0))
                        if source_result.get("rejected"):
                            errors += max(1, source_errors)
                        else:
                            errors += source_errors
                        if source_result.get("rejected"):
                            pass
                        elif source_result.get("duplicate"):
                            duplicates += 1
                        elif source_result.get("stored"):
                            stored += 1
                            if source_result.get("redacted"):
                                redacted += 1
                        continue
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
            except Exception:  # noqa: BLE001 - a single malformed item never fails the run
                # Error isolation — a single malformed item never fails the run.
                errors += 1
                continue
            finally:
                items_ms += (time.perf_counter() - item_start) * 1000.0

        now_iso = datetime.now(timezone.utc).isoformat()
        source_key = f"{connector}:{project or ''}"
        sync_start = time.perf_counter()
        await self.db.record_sync(
            source_key=source_key,
            connector=connector,
            project=project,
            last_synced_at=now_iso,
            fetched=len(items),
            stored=stored,
        )
        bookkeeping_ms = (time.perf_counter() - sync_start) * 1000.0
        self._emit(
            "connector_synced",
            {"connector": connector, "stored": stored, "duplicates": duplicates},
        )
        total_ms = (time.perf_counter() - total_start) * 1000.0
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
            "timing_ms": {
                "items": round(items_ms, 1),
                "bookkeeping": round(bookkeeping_ms, 1),
                "total": round(total_ms, 1),
            },
        }

    async def list_sync_state(self) -> list[dict]:
        """All connector sync bookkeeping rows, most-recent first."""
        return await self.db.list_sync_states()
