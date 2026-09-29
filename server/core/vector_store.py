"""Vector Store — In-memory NumPy cosine similarity search."""

from __future__ import annotations

from typing import Callable, Optional

import numpy as np

from .types import Memory


class VectorStore:
    """NumPy-based in-memory vector store for semantic search (MVP).

    Vectors of any dimension are accepted (e.g. after switching between OpenAI
    1536-d and local 384-d embeddings); search only compares vectors whose
    dimension matches the query, so a mode switch never crashes recall.

    Each dimension keeps a normalised, capacity-doubling row matrix plus an
    ``id -> row`` map, so a search is one ``matrix @ query`` product instead of
    an ``np.stack`` over every candidate on each call. The top-k comes from
    ``argpartition`` (O(n)) rather than a full sort.

    Scalable to ~50K vectors before RAM becomes a concern.
    Migration path: swap this class for Qdrant/Milvus when needed.
    """

    def __init__(self, dimension: int = 384):
        self.dimension = dimension
        self._memories: dict[str, Memory] = {}
        self._row_of: dict[str, tuple[int, int]] = {}  # id -> (dim, row)
        self._id_by_row: dict[int, dict[int, str]] = {}  # dim -> row -> id
        self._matrices: dict[int, np.ndarray] = {}  # dim -> (capacity, dim)
        self._count: dict[int, int] = {}  # dim -> live rows

    @property
    def size(self) -> int:
        return len(self._memories)

    def add(self, memory: Memory) -> None:
        """Add (or replace) a memory in the vector store."""
        emb = memory.embedding
        if not emb:
            return
        vector = np.asarray(emb, dtype=np.float32)
        vector = vector / (np.linalg.norm(vector) + 1e-8)
        dim = vector.shape[0]
        existing = self._row_of.get(memory.id)
        if existing is not None and existing[0] != dim:
            self._drop_row(memory.id)  # re-embedded at a new dimension
        self._store_row(memory.id, dim, vector)
        self._memories[memory.id] = memory
        if self.size == 1:
            self.dimension = dim

    def _store_row(self, memory_id: str, dim: int, vector: np.ndarray) -> None:
        matrix = self._matrices.get(dim)
        if matrix is None:
            matrix = self._matrices[dim] = np.empty((0, dim), dtype=np.float32)
            self._count[dim] = 0
            self._id_by_row[dim] = {}
        existing = self._row_of.get(memory_id)
        if existing is not None:  # same dimension: replace in place
            matrix[existing[1]] = vector
            return
        count = self._count[dim]
        if count >= matrix.shape[0]:
            capacity = max(4, matrix.shape[0] * 2)
            grown = np.empty((capacity, dim), dtype=np.float32)
            grown[:count] = matrix[:count]
            matrix = self._matrices[dim] = grown
        matrix[count] = vector
        self._id_by_row[dim][count] = memory_id
        self._row_of[memory_id] = (dim, count)
        self._count[dim] = count + 1

    def _drop_row(self, memory_id: str) -> None:
        dim, row = self._row_of.pop(memory_id)
        count = self._count[dim] - 1
        matrix = self._matrices[dim]
        id_by_row = self._id_by_row[dim]
        last_id = id_by_row.pop(count)
        if row != count:  # swap the tail row into the freed slot
            matrix[row] = matrix[count]
            id_by_row[row] = last_id
            self._row_of[last_id] = (dim, row)
        self._count[dim] = count

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
        dim = len(query_embedding)
        matrix = self._matrices.get(dim)
        count = self._count.get(dim, 0)
        if matrix is None or count == 0:
            return []
        id_by_row = self._id_by_row[dim]

        query = np.asarray(query_embedding, dtype=np.float32)
        query_norm = query / (np.linalg.norm(query) + 1e-8)

        if predicate is None:
            similarities = matrix[:count] @ query_norm
            rows: Optional[np.ndarray] = None
        else:
            rows = np.fromiter(
                (r for r in range(count) if predicate(self._memories[id_by_row[r]])),
                dtype=np.intp,
            )
            if rows.size == 0:
                return []
            similarities = matrix[rows] @ query_norm

        total = similarities.shape[0]
        k = min(top_k, total)
        if k < total:
            top = np.argpartition(similarities, -k)[-k:]
        else:
            top = np.arange(total)
        order = top[np.argsort(similarities[top])[::-1]]

        return [
            (
                self._memories[id_by_row[top_row if rows is None else rows[top_row]]],
                float(similarities[top_row]),
            )
            for top_row in order
        ]

    def remove(self, memory_id: str) -> bool:
        if memory_id in self._row_of:
            self._drop_row(memory_id)
        self._memories.pop(memory_id, None)
        return True

    def memories(self) -> list[Memory]:
        """Every stored memory, for signals that are not cosine (see
        ``server.core.lexical``). Order is unspecified."""
        return list(self._memories.values())

    def get(self, memory_id: str) -> Optional[Memory]:
        return self._memories.get(memory_id)

    def clear(self) -> None:
        self._memories.clear()
        self._row_of.clear()
        self._id_by_row.clear()
        self._matrices.clear()
        self._count.clear()
