"""Embedder — Multi-mode text embedding.

Modes:
    - "openai"  → text-embedding-3-small via OpenAI API (1536-d)
    - "local"   → all-MiniLM-L6-v2 via sentence-transformers (384-d)
    - "ollama"  → any embedding model served by a local Ollama instance
    - "hash"    → deterministic hash embedding (offline fallback, non-semantic)
    - "auto"    → local-first. It never selects a remote provider merely
                  because an API key happens to be present; local itself
                  falls back to hash when sentence-transformers is missing.
"""

from __future__ import annotations

import asyncio
import os

import httpx
import numpy as np

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "nomic-embed-text")


class Embedder:
    """Produces text embeddings via OpenAI API, Ollama, or local model."""

    def __init__(self, mode: str = "auto", dimension: int = 384):
        self._model = None
        self._http: httpx.AsyncClient | None = None
        self.requested_mode = mode
        self.fallback_reason: str | None = None

        if mode == "auto":
            # Privacy invariant: ambient credentials must never silently turn
            # a local-first configuration into a networked one. OpenAI is
            # selected only when the user explicitly requests mode="openai".
            mode = "local"

        self.resolved_mode = mode
        self.mode = mode
        self.dimension = 1536 if mode == "openai" else dimension

        if mode == "local":
            self._init_local()

    def _client(self) -> httpx.AsyncClient:
        """Reuse one AsyncClient across calls so every embed doesn't pay for a
        fresh TCP/TLS handshake. Created lazily on first network use."""
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=30.0)
        return self._http

    async def aclose(self) -> None:
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()
        self._http = None

    def _init_local(self) -> None:
        """Load the local sentence-transformers model.

        Falls back to a deterministic hash embedding when the library or
        model is unavailable (no torch, no internet). This keeps the
        system fully working with zero heavy dependencies and no API key.
        """
        try:
            from sentence_transformers import SentenceTransformer

            model_name = os.getenv("LOCAL_MODEL", "all-MiniLM-L6-v2")
            self._model = SentenceTransformer(model_name)
            test = self._model.encode("test")
            self.dimension = len(test)
        except Exception as exc:
            self.fallback_reason = (
                "Local embedder unavailable; install with: "
                'pip install "stackmemory[local]"'
            )
            if os.getenv("STACKMEMORY_EMBEDDER_DEBUG", "").strip():
                self.fallback_reason += f" ({exc.__class__.__name__}: {exc})"
            self.mode = "hash"
            self._model = None
            self.dimension = 384

    # ── Public API ────────────────────────────────────────────────

    def identity(self) -> dict:
        """Stable provenance receipt for vectors produced by this instance."""
        if self.mode == "openai":
            model = "text-embedding-3-small"
        elif self.mode == "ollama":
            model = OLLAMA_MODEL
        elif self.mode == "local":
            model = os.getenv("LOCAL_MODEL", "all-MiniLM-L6-v2")
        else:
            model = "stackmemory-hash-v1"
        return {
            "provider": self.mode,
            "model": model,
            "dimension": int(self.dimension),
            "version": "embedding-provenance-v1",
            "requested_mode": self.requested_mode,
        }

    async def embed(self, text: str) -> list[float]:
        if self.mode == "openai":
            return await self._openai_embed(text)
        if self.mode == "ollama":
            return await self._ollama_embed(text)
        if self.mode == "hash":
            return self.hash_embed(text, self.dimension)
        return self._local_embed(text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if self.mode == "openai":
            return await self._openai_embed_batch(texts)
        if self.mode == "ollama":
            return [await self._ollama_embed(t) for t in texts]
        if self.mode == "hash":
            return [self.hash_embed(t, self.dimension) for t in texts]
        return self._local_embed_batch(texts)

    # ── OpenAI ────────────────────────────────────────────────────

    async def _openai_post(self, payload: dict, timeout: float) -> dict:
        """POST to the OpenAI embeddings endpoint with a small retry on
        transient failures (network blips / 429 / 5xx) using exponential
        backoff, so a momentary hiccup doesn't turn every store/recall into a
        500. A persistent failure still raises, surfacing the real error."""
        headers = {
            "Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}",
            "Content-Type": "application/json",
        }
        client = self._client()
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                resp = await client.post(
                    "https://api.openai.com/v1/embeddings",
                    headers=headers,
                    json=payload,
                    timeout=timeout,
                )
                if resp.status_code == 429 or resp.status_code >= 500:
                    resp.raise_for_status()
                resp.raise_for_status()
                return resp.json()
            except (httpx.HTTPError,) as exc:
                last_exc = exc
                if attempt < 2:
                    await asyncio.sleep(0.5 * (2 ** attempt))
        raise last_exc  # type: ignore[misc]

    async def _openai_embed(self, text: str) -> list[float]:
        data = await self._openai_post(
            {"model": "text-embedding-3-small", "input": text}, timeout=30.0
        )
        return data["data"][0]["embedding"]

    async def _openai_embed_batch(self, texts: list[str]) -> list[list[float]]:
        data = await self._openai_post(
            {"model": "text-embedding-3-small", "input": texts}, timeout=60.0
        )
        return [d["embedding"] for d in data["data"]]

    # ── Ollama (fully local, zero API cost) ──────────────────────

    async def _ollama_embed(self, text: str) -> list[float]:
        """Embed via a local Ollama server; falls back to hash if unreachable."""
        try:
            resp = await self._client().post(
                f"{OLLAMA_URL.rstrip('/')}/api/embeddings",
                json={"model": OLLAMA_MODEL, "prompt": text},
            )
            resp.raise_for_status()
            embedding = resp.json()["embedding"]
            self.dimension = len(embedding)
            return embedding
        except (httpx.HTTPError, KeyError):
            # Ollama not running or model missing — degrade to hash so the
            # memory system keeps working instead of erroring every store.
            self.mode = "hash"
            self.dimension = 384
            return self.hash_embed(text, self.dimension)

    # ── Local ────────────────────────────────────────────────────

    def _local_embed(self, text: str) -> list[float]:
        vec = self._model.encode(text, convert_to_numpy=True)
        return vec.tolist()

    def _local_embed_batch(self, texts: list[str]) -> list[list[float]]:
        vecs = self._model.encode(texts, convert_to_numpy=True)
        return [v.tolist() for v in vecs]

    # ── Fallback: deterministic hash embedding (no model needed) ─

    @staticmethod
    def hash_embed(text: str, dim: int = 384) -> list[float]:
        """Ultra-lightweight deterministic embedding using hash.
        Produces non-semantic but deterministic vectors.
        Used as absolute fallback when no model is available."""
        vec = np.zeros(dim, dtype=np.float32)
        for i, ch in enumerate(text[:dim]):
            vec[i] = (ord(ch) % 256) / 256.0
        # Normalise
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec /= norm
        return vec.tolist()
