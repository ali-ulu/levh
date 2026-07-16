"""Vector Store — In-memory NumPy cosine similarity search."""

from __future__ import annotations

from typing import Callable, Optional

import numpy as np

from .types import Memory


class VectorStore:
    """NumPy-based in-memory vector store for semantic search (MVP).

    Holds float32 vectors in a dict keyed by memory ID. Vectors of any
    dimension are accepted (e.g. after switching between OpenAI 1536-d and
    local 384-d embeddings); search only compares vectors whose dimension
    matches the query, so a mode switch never crashes recall.

    Scalable to ~50K vectors before RAM becomes a concern.
    Migration path: swap this class for Qdrant/Milvus when needed.
    """

    def __init__(self, dimension: int = 384):
        self.dimension = dimension
        self._vectors: dict[str, np.ndarray] = {}
        self._memories: dict[str, Memory] = {}

    @property
    def size(self) -> int:
        return len(self._vectors)

    def add(self, memory: Memory) -> None:
        """Add (or replace) a memory in the vector store."""
        emb = memory.embedding
        if not emb:
            return
        self._vectors[memory.id] = np.array(emb, dtype=np.float32)
        self._memories[memory.id] = memory
        if self.size == 1:
            self.dimension = len(emb)

    def add_batch(self, memories: list[Memory]) -> None:
        for m in memories:
            self.add(m)

    def search(
        self,
        query_embedding: list[float],
        top_k: int = 10,
        predicate: Optional[Callable[[Memory], bool]] = None,
    ) -> list[tuple[Memory, float]]:
        """Cosine similarity search. Returns (memory, similarity) pairs.

        Args:
            query_embedding: Query vector.
            top_k: Max results.
            predicate: Optional filter applied BEFORE ranking, so filtered
                searches (per session/project) still return up to top_k results.
        """
        if not self._vectors:
            return []

        query = np.array(query_embedding, dtype=np.float32)
        query_dim = query.shape[0]

        candidate_ids = [
            mid
            for mid, vec in self._vectors.items()
            if vec.shape[0] == query_dim
            and (predicate is None or predicate(self._memories[mid]))
        ]
        if not candidate_ids:
            return []

        query_norm = query / (np.linalg.norm(query) + 1e-8)
        matrix = np.stack([self._vectors[i] for i in candidate_ids])
        norms = matrix / (np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-8)
        similarities = norms @ query_norm

        actual_k = min(top_k, len(candidate_ids))
        top_indices = np.argsort(similarities)[::-1][:actual_k]

        return [
            (self._memories[candidate_ids[idx]], float(similarities[idx]))
            for idx in top_indices
        ]

    def remove(self, memory_id: str) -> bool:
        self._vectors.pop(memory_id, None)
        self._memories.pop(memory_id, None)
        return True

    def get(self, memory_id: str) -> Optional[Memory]:
        return self._memories.get(memory_id)

    def clear(self) -> None:
        self._vectors.clear()
        self._memories.clear()
