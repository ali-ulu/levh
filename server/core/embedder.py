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
import importlib.util
import logging
import os
import time

import httpx
import numpy as np

from server.core import metrics
from server.core.env import get_env

logger = logging.getLogger("levh.embedder")

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "nomic-embed-text")

LOCAL_UNAVAILABLE_REASON = (
    "Local embedder unavailable; install with: "
    'pip install "levh[local]"'
)

#: Reasons this process has already warned about from the model-free probe.
#:
#: Readiness polls every 30 seconds (Docker ``HEALTHCHECK``) and the probe
#: sees the same would-be fallback on every one of them. The issue asked for
#: *one* warning line in ``serve.err.log``, not a second log flood — so the
#: report is emitted once per distinct reason per process (issue #519).
_PROBE_WARNED: set[str] = set()


def local_embedder_available() -> bool:
    """Cheap availability check — does not import torch or load a model."""
    try:
        return importlib.util.find_spec("sentence_transformers") is not None
    except (ImportError, ValueError):
        return False


def probe_embedder_status(mode: str) -> dict:
    """Resolve an embedder mode to its effective status without loading a model.

    ``Embedder.__init__`` answers the same question, but only after paying
    for it — and the engine creates the embedder lazily, on the first store
    or recall. A health probe that ran before that point used to report the
    *requested* mode (``auto``) as if it were the effective one, so a server
    whose first embed would silently land on hash answered ``ready`` right
    up until the degradation actually happened (#519). This check is cheap
    enough to run on every probe: it inspects availability, never imports
    torch and never loads a model.

    It cannot see runtime failures (an Ollama endpoint that is down, a
    model download that fails); those are recorded on the instance by the
    real initialization path and reported the same way.
    """
    requested = (mode or "auto").strip().lower() or "auto"
    effective = "local" if requested == "auto" else requested
    reason: str | None = None
    if effective == "local" and not local_embedder_available():
        effective = "hash"
        reason = LOCAL_UNAVAILABLE_REASON
    # A fallback is a *semantic* request that resolved to hash. Asking for
    # hash explicitly is a choice and never reported as degraded.
    fallback = effective == "hash" and requested != "hash"
    reason = reason if fallback else None
    if reason and reason not in _PROBE_WARNED:
        _PROBE_WARNED.add(reason)
        logger.warning(
            "Embedder requested=%s would resolve to non-semantic hash: %s",
            requested,
            reason,
        )
    return {
        "requested_mode": requested,
        "effective_mode": effective,
        "fallback": fallback,
        "fallback_reason": reason,
        # A semantic request that is *not* a fallback still has to load its
        # model before it can embed. The probe cannot see whether that load has
        # happened (that state lives on the instance), so it reports the
        # resolution as ready and the caller overlays the instance's own
        # ``warm_state`` when one exists (issue #525).
        "warm_state": "ready",
    }


class Embedder:
    """Produces text embeddings via OpenAI API, Ollama, or local model.

    ``warm_state`` tracks whether a local model has been loaded, so a surface
    can say *warming* instead of looking hung: importing torch and building
    ``all-MiniLM-L6-v2`` blocks for ~2.5 minutes on a cold machine, and the
    engine used to pay that inline on the first store/recall (issue #525).
    """

    def __init__(self, mode: str = "auto", dimension: int = 384, *, defer_local_load: bool = False):
        self._model = None
        self._http: httpx.AsyncClient | None = None
        self._warm_lock: asyncio.Lock | None = None
        self.requested_mode = mode
        self.fallback_reason: str | None = None
        #: ``"ready"`` (nothing to load, or loaded), ``"cold"`` (a local model
        #: is available but not loaded yet), ``"warming"`` (load in flight) or
        #: ``"unavailable"`` (the local model could not be used; hash fallback).
        self.warm_state = "ready"
        #: How long the local load took, once it has run. The machine-specific
        #: number a user needs in order to file this kind of report instead of
        #: guessing (issue #525).
        self.warm_seconds: float | None = None

        if mode == "auto":
            # Privacy invariant: ambient credentials must never silently turn
            # a local-first configuration into a networked one. OpenAI is
            # selected only when the user explicitly requests mode="openai".
            mode = "local"

        self.resolved_mode = mode
        self.mode = mode
        self.dimension = 1536 if mode == "openai" else dimension

        if mode == "local":
            if defer_local_load:
                # Resolve availability cheaply now (so ``mode``/``is_semantic``
                # are stable from construction) and load the model later, off
                # the event loop, via ``warm()``.
                self._defer_local()
            else:
                self._init_local()
                self.warm_state = "ready" if self.mode == "local" else "unavailable"

    def needs_warm(self) -> bool:
        """Whether a local model is available but not loaded yet."""
        return self.mode == "local" and self._model is None

    async def warm(self) -> None:
        """Load the local model off the event loop, once (issue #525).

        The import-and-build cost is paid in a worker thread so the loop keeps
        serving other requests, and concurrent callers coalesce on the lock
        instead of each paying for a second copy of the model. A no-op once
        the model is loaded, and the load itself records why on failure, so
        this never raises.
        """
        if not self.needs_warm():
            return
        if self._warm_lock is None:
            self._warm_lock = asyncio.Lock()
        async with self._warm_lock:
            if not self.needs_warm():
                return
            self.warm_state = "warming"
            try:
                await asyncio.to_thread(self._init_local)
            except Exception as exc:  # noqa: BLE001 - a failed load degrades, it never escapes
                # ``_init_local`` already records its own failures, but a raise
                # that escapes it (a worker-thread or import-time failure) must
                # not reach the request path: ``embed`` awaits this, and the
                # documented contract is a hash fallback, not a 500.
                self._degrade_to_hash(f"{exc.__class__.__name__}: {exc}")
            if self.mode == "local":
                self.warm_state = "ready"
            else:
                self.warm_state = "unavailable"
                # The deferred load resolved to hash: the operator-visible
                # signal that semantic search silently became non-semantic.
                # The eager construction path logs the same line; this covers
                # the deferred load, which is where the failure now lands
                # (issue #525).
                logger.warning(
                    "Embedder requested=%s resolved=hash: %s",
                    self.requested_mode,
                    self.fallback_reason,
                )

    def _degrade_to_hash(self, detail: str) -> None:
        """Record why a semantic embedder could not load and switch to hash."""
        reason = LOCAL_UNAVAILABLE_REASON
        if detail and get_env("LEVH_EMBEDDER_DEBUG", "").strip():
            reason += f" ({detail})"
        self.fallback_reason = reason
        self.mode = "hash"
        self._model = None
        self.dimension = 384
        metrics.inc("levh_embedder_fallback_total")

    def _defer_local(self) -> None:
        """Resolve local availability without importing torch (issue #525).

        Mirrors what the eager path would decide — a missing
        sentence-transformers is a hash fallback, recorded immediately — but
        never touches the heavy import, so construction stays cheap and the
        engine can publish the instance before any request needs it.
        """
        if local_embedder_available():
            self.warm_state = "cold"
            return
        self._degrade_to_hash("")
        self.warm_state = "unavailable"

    @property
    def fallback(self) -> bool:
        """Whether a semantic request silently resolved to non-semantic hash.

        An explicit ``hash`` request is a choice, not a fallback, and reports
        ``False``: the operator asked for exactly what is running.
        """
        return (
            bool(self.fallback_reason)
            and self.mode == "hash"
            and self.requested_mode != "hash"
        )

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

        Blocking by design: it imports torch and builds the model, ~2.5
        minutes cold (issue #525). Callers on the event loop reach it through
        :meth:`warm`, which runs it in a worker thread.
        """
        start = time.perf_counter()
        try:
            from sentence_transformers import SentenceTransformer

            model_name = os.getenv("LOCAL_MODEL", "all-MiniLM-L6-v2")
            self._model = SentenceTransformer(model_name)
            test = self._model.encode("test")
            self.dimension = len(test)
            # The machine-specific cost, logged once so an operator filing a
            # "the server hung on the first write" report has the number
            # instead of a guess (issue #525).
            self.warm_seconds = time.perf_counter() - start
            logger.info(
                "embedder ready after %.1fs (model=%s, dimension=%d)",
                self.warm_seconds,
                model_name,
                int(self.dimension),
            )
        except Exception as exc:  # noqa: BLE001 - no model is a supported mode; record why and degrade
            self._degrade_to_hash(f"{exc.__class__.__name__}: {exc}")

    # ── Public API ────────────────────────────────────────────────

    @property
    def is_semantic(self) -> bool:
        """Whether the resolved mode produces meaning-bearing vectors.

        Only ``hash`` is not. Any caller that treats cosine as a
        duplicate/relatedness/relevance signal must consult this first: the
        hash vectors are positional, so their cosine reflects character
        positions rather than meaning and is unusable for those decisions.
        Recall falls back to word overlap (``server.core.lexical``) and the
        admission gate to exact-duplicate detection under this mode.
        """
        return self.mode != "hash"

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
        # A deferred local model: the first embed pays for the load, but in a
        # worker thread, so a concurrent request is not frozen behind it
        # (issue #525). ``warm()`` is a no-op once the model is loaded, and a
        # failed load has already flipped ``mode`` to hash.
        await self.warm()
        if self.mode == "hash":
            return self.hash_embed(text, self.dimension)
        return self._local_embed(text)

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
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt < 2:
                    await asyncio.sleep(0.5 * (2 ** attempt))
        raise last_exc  # type: ignore[misc]

    async def _openai_embed(self, text: str) -> list[float]:
        data = await self._openai_post(
            {"model": "text-embedding-3-small", "input": text}, timeout=30.0
        )
        return data["data"][0]["embedding"]

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
            # The reason is recorded and logged exactly like the local
            # degrade: a fallback nobody can see is a fallback nobody can
            # fix (#519), and a health probe must not report ready while
            # ranking has quietly become non-semantic.
            self.fallback_reason = (
                "Ollama embedder unavailable; falling back to hash"
            )
            self.mode = "hash"
            self.dimension = 384
            metrics.inc("levh_embedder_fallback_total")
            logger.warning(
                "Ollama embedder unreachable at %s; degrading to hash "
                "embeddings: %s",
                OLLAMA_URL,
                self.fallback_reason,
            )
            return self.hash_embed(text, self.dimension)

    # ── Local ────────────────────────────────────────────────────

    def _local_embed(self, text: str) -> list[float]:
        vec = self._model.encode(text, convert_to_numpy=True)
        return vec.tolist()

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
