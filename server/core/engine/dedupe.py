"""Duplicate detection and consolidation.

Part of :class:`server.core.memory_engine.MemoryEngine`, split out to keep
each file readable. Mixins rather than separate services: the methods use the
engine's own state throughout, and moving the bodies unchanged is what makes
the split verifiable.
"""

from __future__ import annotations


from ..types import (
    Memory,
)


class MemoryDedupeMixin:
    """Duplicate detection and consolidation."""

    async def find_duplicates(
        self, similarity_threshold: float = 0.95, project: str | None = None
    ) -> list[list[Memory]]:
        """Find groups of near-duplicate memories by embedding similarity."""
        import numpy as np

        memories = await self.episodic.search(project=project, limit=10000)
        with_emb = [m for m in memories if m.embedding]
        groups: list[list[Memory]] = []
        used: set[str] = set()

        by_dim: dict[int, list[Memory]] = {}
        for m in with_emb:
            by_dim.setdefault(len(m.embedding), []).append(m)

        for dim_memories in by_dim.values():
            if len(dim_memories) < 2:
                continue
            matrix = np.stack(
                [np.array(m.embedding, dtype=np.float32) for m in dim_memories]
            )
            norms = matrix / (np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-8)
            sim = norms @ norms.T
            n = len(dim_memories)
            for i in range(n):
                if dim_memories[i].id in used:
                    continue
                group = [dim_memories[i]]
                for j in range(i + 1, n):
                    if dim_memories[j].id in used:
                        continue
                    if float(sim[i, j]) >= similarity_threshold:
                        group.append(dim_memories[j])
                        used.add(dim_memories[j].id)
                if len(group) > 1:
                    used.add(dim_memories[i].id)
                    groups.append(group)
        return groups

    async def dedupe(
        self, similarity_threshold: float = 0.95, project: str | None = None
    ) -> int:
        """Delete near-duplicates, keeping the most important / most recent of each group."""
        groups = await self.find_duplicates(similarity_threshold, project)
        removed = 0
        for group in groups:
            # Keep pinned first, then highest importance, then newest.
            group.sort(
                key=lambda m: (m.pinned, m.importance, m.created_at), reverse=True
            )
            for duplicate in group[1:]:
                if duplicate.pinned:
                    continue  # never auto-delete pinned memories
                if await self.forget(duplicate.id):
                    removed += 1
        return removed

    async def consolidate_memories(
        self,
        similarity_threshold: float = 0.82,
        min_age_days: int = 7,
        min_cluster_size: int = 2,
        project: str | None = None,
        dry_run: bool = True,
    ) -> dict:
        """Compress clusters of *related* older memories into a single
        consolidated memory — modelled on how human memory consolidates during
        sleep: many similar episodes collapse into one durable gist, the raw
        episodes fade.

        Unlike ``dedupe`` (which only removes near-identical duplicates at a
        high threshold and keeps one verbatim), this uses a lower similarity
        threshold to group *related* memories and replaces the whole cluster
        with an LLM/extractive summary. The raw originals are preserved inside
        the consolidated memory's ``metadata.consolidated_from`` so nothing is
        lost — they can be recovered — but they stop cluttering active recall.

        Safeguards: pinned memories are never consolidated, and only memories
        older than ``min_age_days`` are eligible so recent working memory is
        left intact. ``dry_run=True`` (default) previews clusters without
        changing anything.

        Returns a dict with the clusters found and, when applied, how many
        consolidated memories were created and how many originals archived.
        """
        from datetime import datetime, timedelta, timezone

        from ..summarizer import summarize_texts

        now = datetime.now(timezone.utc)
        age_cutoff = (now - timedelta(days=max(min_age_days, 0))).isoformat()
        min_cluster_size = max(min_cluster_size, 2)

        groups = await self.find_duplicates(similarity_threshold, project)
        client = self._embedder._http if self._embedder is not None else None

        clusters: list[dict] = []
        consolidated_count = 0
        removed_count = 0

        for group in groups:
            # Eligible members: not pinned, older than the age cutoff, and not
            # already a consolidation output (avoid recompressing summaries).
            members = [
                m
                for m in group
                if not m.pinned
                and (m.created_at or "") <= age_cutoff
                and "consolidated" not in (m.tags or [])
            ]
            if len(members) < min_cluster_size:
                continue

            members.sort(key=lambda m: m.created_at or "")
            texts = [m.content for m in members if m.content]
            if not texts:
                continue
            summary_text = (await summarize_texts(texts, mode="auto", client=client)).strip()
            if not summary_text:
                continue

            cluster_project = next((m.project for m in members if m.project), None)
            cluster = {
                "size": len(members),
                "project": cluster_project,
                "summary": summary_text[:500],
                "member_ids": [m.id for m in members],
                "sample": [m.content.split("\n", 1)[0][:80] for m in members[:3]],
            }

            if not dry_run:
                importance = max((m.importance for m in members), default=0.5)
                consolidated = await self.store(
                    content=(
                        f"Consolidated memory ({len(members)} related memories):\n"
                        f"{summary_text}"
                    ),
                    importance=min(max(importance, 0.5), 1.0),
                    tags=["consolidated"],
                    project=cluster_project,
                    source="consolidation",
                    memory_type="episodic",
                    metadata={
                        # Lineage proves which records were compressed without
                        # retaining a second undeletable copy of their content.
                        "consolidated_from": [
                            {
                                "id": m.id,
                                "created_at": m.created_at,
                                "content_sha256": __import__("hashlib").sha256(
                                    (m.content or "").encode("utf-8")
                                ).hexdigest(),
                            }
                            for m in members
                        ],
                        # A summary derived exclusively from demo records remains
                        # demo-tagged so the safe onboarding cleanup can remove it.
                        "demo": bool(members) and all(
                            bool((m.metadata or {}).get("demo")) for m in members
                        ),
                    },
                )
                cluster["consolidated_id"] = consolidated.id
                consolidated_count += 1
                for m in members:
                    if await self.forget(m.id):
                        removed_count += 1

            clusters.append(cluster)

        if not dry_run and (consolidated_count or removed_count):
            self._emit(
                "consolidated_memories",
                {"consolidated": consolidated_count, "archived": removed_count},
            )

        return {
            "dry_run": dry_run,
            "clusters_found": len(clusters),
            "consolidated": consolidated_count,
            "archived": removed_count,
            "clusters": clusters,
        }
