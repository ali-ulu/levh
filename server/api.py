"""LEVH API — FastAPI server with REST + WebSocket + MCP SSE.

Serves the dashboard frontend and exposes the memory engine via HTTP/WebSocket.
Mounts MCP SSE under /api/mcp; the stream endpoint is /api/mcp/sse.

Usage:
    uvicorn server.api:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath
from typing import Optional

logger = logging.getLogger("levh.api")

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.auth import (
    AUTH_RATE_LIMIT,
    AUTH_RATE_LIMIT_WINDOW_SECONDS,
    RemoteAccessBoundaryMiddleware,
    constant_time_token_matches,
    shared_auth_limiter,
)
from server.core import engine_provider, llm_policy
from server.core.env import get_env
from server.core.memory_engine import MemoryEngine
from server.core.rate_limit import SlidingWindowRateLimiter
from server.core.types import (
    Memory,
    MemoryStats,
    RecallRequest,
    RecallResult,
    Session,
)

# ── Global engine ───────────────────────────────────────────────────
# _engine/_initialized are kept as module globals for test injection;
# they proxy the shared engine in server.core.engine_provider so the
# REST API and the mounted MCP SSE server always use the SAME engine.

_engine: MemoryEngine | None = None
_initialized = False


async def get_engine() -> MemoryEngine:
    global _engine, _initialized
    if _engine is None:
        _engine = engine_provider.get_engine()
    else:
        # Keep provider in sync when tests inject a custom engine here.
        engine_provider.set_engine(_engine)
    await _engine.initialize()  # idempotent
    if not _initialized:
        _subscribe_broadcaster(_engine)
        _initialized = True
    return _engine


# ── Live event broadcast (WebSocket) ─────────────────────────────────

_ws_clients: set[WebSocket] = set()
_event_loop: asyncio.AbstractEventLoop | None = None
_subscribed_engines: set[int] = set()


def _subscribe_broadcaster(engine: MemoryEngine) -> None:
    if id(engine) in _subscribed_engines:
        return
    engine.subscribe(_on_engine_event)
    _subscribed_engines.add(id(engine))


def _on_engine_event(event: str, payload: dict) -> None:
    """Engine event listener → fan out to connected WebSocket clients."""
    if not _ws_clients or _event_loop is None:
        return
    message = json.dumps({"type": "event", "event": event, "payload": payload}, default=str)
    for ws in list(_ws_clients):
        try:
            asyncio.run_coroutine_threadsafe(ws.send_text(message), _event_loop)
        except RuntimeError:
            _ws_clients.discard(ws)


# ── App lifespan ─────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _event_loop
    _event_loop = asyncio.get_running_loop()
    engine = await get_engine()
    yield
    await engine.shutdown()


# ── FastAPI app ─────────────────────────────────────────────────────

app = FastAPI(
    title="LEVH API",
    version="2.28.0",
    description="Local-first memory layer for AI agents and humans",
    lifespan=lifespan,
)

# CORS: this service is normally a *local* single-user tool, so a wildcard
# origin means any website the user visits can read their whole memory store
# from the browser. Default to localhost origins and let deployments widen it
# explicitly via LEVH_CORS_ORIGINS ("*" to opt back into wildcard).
_DEFAULT_CORS = (
    "http://localhost:3000,http://127.0.0.1:3000,"
    "http://localhost:8000,http://127.0.0.1:8000"
)
_cors_env = get_env("LEVH_CORS_ORIGINS", _DEFAULT_CORS).strip()
_cors_origins = (
    ["*"] if _cors_env == "*" else [o.strip() for o in _cors_env.split(",") if o.strip()]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Optional shared-secret gate. When LEVH_TOKEN is set, every /api/*
# request (except the health check) must present it via the
# X-LEVH-Token header. The legacy X-StackMemory-Token header remains accepted
# for compatibility. Unset (default) keeps the tool zero-config for
# purely local use.
_API_TOKEN = get_env("LEVH_TOKEN", "").strip()
app.add_middleware(RemoteAccessBoundaryMiddleware, token=_API_TOKEN)
_AUTH_RATE_LIMIT = AUTH_RATE_LIMIT
try:
    _API_RATE_LIMIT = int(get_env("LEVH_API_RATE_LIMIT", "120"))
except ValueError:
    _API_RATE_LIMIT = 120
_RATE_LIMIT_WINDOW = AUTH_RATE_LIMIT_WINDOW_SECONDS

_auth_limiter = shared_auth_limiter
_api_limiter = SlidingWindowRateLimiter(_API_RATE_LIMIT, _RATE_LIMIT_WINDOW)


def _request_client_key(request: Request) -> str:
    # Do not trust X-Forwarded-For by default; deployments behind a trusted
    # reverse proxy should terminate/rate-limit there as well.
    return request.client.host if request.client else "unknown"


@app.middleware("http")
async def _require_token(request: Request, call_next):
    if (
        _API_TOKEN
        and request.url.path.startswith("/api/")
        and request.url.path != "/api/health"
    ):
        client_key = _request_client_key(request)
        supplied = (
            request.headers.get("X-LEVH-Token")
            or request.headers.get("X-StackMemory-Token", "")
        )
        if not constant_time_token_matches(supplied, _API_TOKEN):
            allowed, retry_after = _auth_limiter.allow(client_key)
            if not allowed:
                return JSONResponse(
                    {"detail": "too many authentication attempts"},
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        allowed, retry_after = _api_limiter.allow(client_key)
        if not allowed:
            return JSONResponse(
                {"detail": "rate limit exceeded"},
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )
    return await call_next(request)


# Public demo mode: when LEVH_PUBLIC_DEMO=true, allow only read-only (GET) access
# to /api/* endpoints. Block all mutating methods (POST, PUT, PATCH, DELETE) and
# sensitive export endpoints. This prevents anonymous visitors from modifying or
# exporting the shared demo database.
_PUBLIC_DEMO = get_env("LEVH_PUBLIC_DEMO", "").strip().lower() == "true"
_PUBLIC_DEMO_BLOCKED_PATHS = {
    "/api/export/full.json",
    "/api/export/full.sqlite",
    "/api/export/full.pdf",
}


@app.middleware("http")
async def _public_demo_guard(request: Request, call_next):
    if (
        _PUBLIC_DEMO
        and request.url.path.startswith("/api/")
        and request.url.path != "/api/health"
    ):
        # Allow safe read-only methods on most endpoints
        if request.method in ("GET", "HEAD", "OPTIONS"):
            # But block sensitive export endpoints even for GET
            if request.url.path in _PUBLIC_DEMO_BLOCKED_PATHS:
                return JSONResponse(
                    {"detail": "forbidden in public demo mode"},
                    status_code=403,
                )
            return await call_next(request)
        # Block all mutating methods
        return JSONResponse(
            {"detail": "forbidden in public demo mode: mutating endpoint"},
            status_code=403,
        )
    return await call_next(request)


# ── Request/Response DTOs ───────────────────────────────────────────

class StoreRequest(BaseModel):
    content: str
    importance: float = 0.5
    tags: list[str] = []
    session_id: Optional[str] = None
    project: Optional[str] = None
    source: Optional[str] = None
    pinned: bool = False
    memory_type: str = "short_term"
    metadata: dict = {}
    force: bool = False
    min_length: int = 3


class UpdateRequest(BaseModel):
    content: Optional[str] = None
    importance: Optional[float] = None
    tags: Optional[list[str]] = None
    project: Optional[str] = None
    pinned: Optional[bool] = None


class CreateSessionRequest(BaseModel):
    name: str = "Untitled Session"
    metadata: dict = {}


class ImportRequest(BaseModel):
    data: list[dict]


class OnboardingMCPConfigRequest(BaseModel):
    client: str = "claude"
    profile: str = "work"


class DemoCleanupRequest(BaseModel):
    confirm: bool = False


class BackupRequest(BaseModel):
    passphrase: str = ""


class RestoreRequest(BaseModel):
    content_b64: str
    passphrase: str = ""
    replace: bool = False


class PinRequest(BaseModel):
    pinned: bool = True


class ContextFileRequest(BaseModel):
    project: Optional[str] = None
    style: str = "claude"  # "claude" | "cursor"


class DedupeRequest(BaseModel):
    similarity_threshold: float = 0.95
    project: Optional[str] = None
    dry_run: bool = True


class ConsolidateRequest(BaseModel):
    similarity_threshold: float = 0.82
    min_age_days: int = 7
    min_cluster_size: int = 2
    project: Optional[str] = None
    dry_run: bool = True


class ReviewRequest(BaseModel):
    action: str
    snooze_days: int = 7
    reason: str = ""


class RedactAllRequest(BaseModel):
    dry_run: bool = True


class AskRequest(BaseModel):
    question: str
    top_k: int = 6
    project: Optional[str] = None
    session_id: Optional[str] = None
    min_importance: float = 0.0


class AdmissionEvalRequest(BaseModel):
    content: str
    project: Optional[str] = None
    min_length: int = 3


class AdmitRequest(BaseModel):
    content: str
    importance: float = 0.5
    tags: list[str] = []
    session_id: Optional[str] = None
    project: Optional[str] = None
    source: Optional[str] = None
    pinned: bool = False
    memory_type: str = "short_term"
    metadata: dict = {}
    force: bool = False
    min_length: int = 3


# ── REST Routes: Memories ──────────────────────────────────────────


@app.post("/api/memories")
async def store_memory(req: StoreRequest):
    """Default product write path: admission gate before persistence.

    ``force=true`` is an explicit audited override for administrative recovery;
    the admission decision is still recorded in memory metadata.
    """
    engine = await get_engine()
    try:
        result = await engine.admit_memory(
            content=req.content,
            importance=req.importance,
            tags=req.tags,
            session_id=req.session_id,
            project=req.project,
            source=req.source or "dashboard",
            pinned=req.pinned,
            memory_type=req.memory_type,
            metadata=req.metadata,
            force=req.force,
            min_length=req.min_length,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if not result["stored"]:
        raise HTTPException(
            status_code=409,
            detail={"message": "memory not stored by admission gate", "decision": result["decision"]},
        )
    return result["memory"]


@app.get("/api/memories")
async def list_memories(
    memory_type: str = "",
    session_id: str = "",
    project: str = "",
    source: str = "",
    tag: str = "",
    pinned: str = "",
    q: str = "",
    min_importance: float = 0.0,
    limit: int = 50,
    offset: int = 0,
):
    engine = await get_engine()
    memories = await engine.list_memories(
        memory_type=memory_type or None,
        session_id=session_id or None,
        project=project or None,
        source=source or None,
        tag=tag or None,
        pinned={"true": True, "false": False}.get(pinned.lower(), None),
        min_importance=min_importance if min_importance > 0 else None,
        content_like=q or None,
        limit=limit,
        offset=offset,
    )
    return [m.model_dump(exclude={"embedding"}) for m in memories]


# NOTE: declared before /api/memories/{memory_id} so "fading" is not
# swallowed by the path parameter.
@app.get("/api/memories/fading")
async def list_fading_memories(threshold: float = 0.35, project: str = "", limit: int = 20):
    """Memories predicted to be nearly forgotten — the review queue."""
    engine = await get_engine()
    fading = await engine.list_fading(
        threshold=max(0.01, min(0.99, threshold)),
        project=project or None,
        limit=min(max(limit, 1), 100),
    )
    return [
        {**m.model_dump(exclude={"embedding"}), "retention": retention}
        for m, retention in fading
    ]


# NOTE: declared before /api/memories/{memory_id} so "review" is not
# swallowed by the path parameter.
@app.get("/api/memories/review")
async def get_review_queue(threshold: float = 0.5, project: str = "", limit: int = 50):
    """Spaced-repetition review queue — fading, unpinned, un-snoozed memories
    due for a keep/reinforce/weaken/pin/forget/snooze decision."""
    engine = await get_engine()
    return {
        "review": await engine.review_queue(
            threshold=max(0.01, min(0.99, threshold)),
            project=project or None,
            limit=min(max(limit, 1), 200),
        )
    }


@app.post("/api/memories/{memory_id}/review")
async def review_memory(memory_id: str, req: ReviewRequest):
    """Apply a spaced-repetition review decision to a memory."""
    engine = await get_engine()
    try:
        result = await engine.apply_review(
            memory_id, req.action, snooze_days=req.snooze_days, reason=req.reason
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("error", "memory not found"))
    return result


# NOTE: declared before /api/memories/{memory_id} so "audit-secrets" is not
# swallowed by the path parameter.
@app.get("/api/memories/audit-secrets")
async def get_audit_secrets():
    """Read-only scan for secrets (credentials, tokens) that slipped into
    stored memories before the admission gate existed."""
    engine = await get_engine()
    return {"audit": await engine.audit_secrets()}


# NOTE: declared before /api/memories/{memory_id} so "redact-all" is not
# swallowed by the path parameter.
@app.post("/api/memories/redact-all")
async def redact_all_secrets(req: RedactAllRequest):
    """Bulk redaction of secrets across stored memories. dry_run=true
    (default) only previews; set false to rewrite every flagged memory."""
    engine = await get_engine()
    return await engine.redact_all_secrets(dry_run=req.dry_run)


# NOTE: declared before /api/memories/{memory_id} so "low-trust" is not
# swallowed by the path parameter.
@app.get("/api/memories/low-trust")
async def get_low_trust_memories(threshold: float = 0.4, limit: int = 50):
    """Stored memories whose provenance/trust confidence is below
    ``threshold`` (least confident first). Run trust/recompute first to
    populate. Provenance is NOT truth — it does not change H-score ranking."""
    engine = await get_engine()
    return {"low_trust": await engine.list_low_trust(threshold=threshold, limit=limit)}


# NOTE: declared before /api/memories/{memory_id} so "trust" is not
# swallowed by the path parameter.
@app.post("/api/memories/trust/recompute")
async def recompute_trust_scores():
    """Compute and persist the provenance/trust score for every memory."""
    engine = await get_engine()
    return await engine.recompute_trust_scores()


@app.post("/api/seed-demo")
async def seed_demo(force: bool = False):
    """Populate an empty store with a deterministic demo corpus (onboarding).
    Refuses to run on a non-empty store unless ``force=true``."""
    engine = await get_engine()
    return await engine.seed_demo(force=force)


@app.get("/api/onboarding/status")
async def get_onboarding_status():
    """Real first-run readiness derived from local storage/configuration."""
    engine = await get_engine()
    return await engine.onboarding_status()


@app.post("/api/onboarding/mcp-config")
async def generate_onboarding_mcp_config(req: OnboardingMCPConfigRequest):
    """Generate a focused MCP client config without persisting secrets."""
    from server.configs import PLATFORMS, generate_config, normalize_platform, render_config
    from server.tools.profiles import UnknownProfileError, profile_counts, resolve_profile

    try:
        platform = normalize_platform(req.client)
        profile = resolve_profile(req.profile)
    except (ValueError, UnknownProfileError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    from server.core.runtime_config import resolve_runtime_config, runtime_env

    runtime = resolve_runtime_config()
    cfg = generate_config(
        platform,
        project_path=".",
        profile=profile,
        **runtime_env(runtime),
    )

    # Persist only privacy-safe onboarding status so the dashboard can reflect
    # that an MCP client was configured. The generated config itself is not
    # written here because it may contain local paths or future credentials.
    from server.core.onboarding import write_receipt
    from server.core.dogfood import dogfood_enabled

    engine = await get_engine()
    status = await engine.onboarding_status()
    receipt = write_receipt(
        database_ready=True,
        first_memory_ready=status["memory_count"] > 0,
        mcp_client=req.client,
        mcp_profile=profile,
        demo_mode=bool(status["demo_seeded"]),
        dogfood_enabled=dogfood_enabled(),
    )

    return {
        "client": req.client,
        "platform": platform,
        "profile": profile,
        "tool_count": profile_counts()[profile],
        "profiles_are_security_boundary": False,
        "warning": (
            "MCP profiles reduce the advertised tool surface; they are not "
            "an authorization or security boundary."
        ),
        "onboarding_receipt_written": True,
        "onboarding_ready": receipt["first_memory_ready"],
        "config": cfg,
        # Not every client reads JSON — Codex takes TOML and Hermes YAML — so
        # the dashboard renders this text rather than JSON-encoding `config`.
        "config_text": render_config(platform, cfg),
        "config_path": PLATFORMS[platform]["file_path"],
    }


@app.post("/api/onboarding/remove-demo")
async def remove_onboarding_demo(req: DemoCleanupRequest):
    """Remove only metadata.demo=true memories using the audited purge path."""
    if not req.confirm:
        raise HTTPException(status_code=422, detail="confirmation required")
    engine = await get_engine()
    return await engine.remove_demo_data()


@app.get("/api/memories/{memory_id}")
async def get_memory(memory_id: str):
    engine = await get_engine()
    mem = await engine.get_memory(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="memory not found")
    return mem.model_dump()


@app.put("/api/memories/{memory_id}")
async def update_memory(memory_id: str, req: UpdateRequest):
    engine = await get_engine()
    mem = await engine.update_memory(
        memory_id=memory_id,
        content=req.content,
        importance=req.importance,
        tags=req.tags,
        project=req.project,
        pinned=req.pinned,
    )
    if not mem:
        raise HTTPException(status_code=404, detail="memory not found")
    return mem.model_dump()


@app.patch("/api/memories/{memory_id}/pin")
async def pin_memory(memory_id: str, req: PinRequest):
    engine = await get_engine()
    mem = await engine.set_pinned(memory_id, req.pinned)
    if not mem:
        raise HTTPException(status_code=404, detail="memory not found")
    return mem.model_dump(exclude={"embedding"})


@app.post("/api/memories/{memory_id}/reinforce")
async def reinforce_memory(memory_id: str):
    """Manually strengthen a memory — resets its decay clock and grows its
    stability, the same reinforcement that happens automatically on recall."""
    engine = await get_engine()
    mem = await engine.reinforce_memory(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="memory not found")
    return mem.model_dump(exclude={"embedding"})


class FeedbackRequest(BaseModel):
    helpful: bool


@app.post("/api/memories/{memory_id}/feedback")
async def memory_feedback(memory_id: str, req: FeedbackRequest):
    """Learn from recall outcomes: helpful=true reinforces the memory,
    helpful=false weakens it so wrong/stale information fades out fast."""
    engine = await get_engine()
    mem = await engine.memory_feedback(memory_id, req.helpful)
    if not mem:
        raise HTTPException(status_code=404, detail="memory not found")
    return mem.model_dump(exclude={"embedding"})


@app.post("/api/memories/{memory_id}/redact")
async def redact_memory(memory_id: str):
    """Strip secrets from an already-stored memory in place, recorded
    auditably in its metadata's redaction_history."""
    engine = await get_engine()
    result = await engine.redact_memory(memory_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("error", "memory not found"))
    return result


@app.post("/api/memories/{memory_id}/purge")
async def purge_memory(memory_id: str):
    """Hard-delete a memory across every layer and verify nothing survives.
    Pinned memories are purged too — this is a deliberate human action."""
    engine = await get_engine()
    return await engine.purge_memory(memory_id)


@app.get("/api/memories/{memory_id}/forgetting-curve")
async def get_forgetting_curve(memory_id: str, days: int = 30):
    """Predicted retention curve for a memory — powers the 'memory strength'
    visualization in the dashboard's detail drawer."""
    engine = await get_engine()
    curve = await engine.get_forgetting_curve(memory_id, days=min(max(days, 1), 365))
    if not curve:
        raise HTTPException(status_code=404, detail="memory not found")
    return curve


@app.get("/api/memories/{memory_id}/trust")
async def get_memory_trust(memory_id: str):
    """Provenance/trust breakdown for a memory — explainable, deterministic,
    NOT truth, and independent of H-score recall ranking."""
    engine = await get_engine()
    result = await engine.get_trust(memory_id)
    if result is None:
        raise HTTPException(status_code=404, detail="memory not found")
    return result


@app.get("/api/memories/{memory_id}/related")
async def related_memories(memory_id: str, top_k: int = 5):
    """Memories most similar to this one — the 'related memories' graph edge,
    computed live from embeddings. Powers 'see also' in the detail drawer."""
    engine = await get_engine()
    if not await engine.get_memory(memory_id):
        raise HTTPException(status_code=404, detail="memory not found")
    related = await engine.get_related(memory_id, top_k=min(max(top_k, 1), 20))
    return {
        "memory_id": memory_id,
        "related": [
            {**m.model_dump(exclude={"embedding"}), "similarity": round(sim, 4)}
            for m, sim in related
        ],
    }


@app.delete("/api/memories/{memory_id}")
async def delete_memory(memory_id: str):
    engine = await get_engine()
    success = await engine.forget(memory_id)
    return {"deleted": success}


@app.post("/api/memories/recall")
async def recall_memories(req: RecallRequest):
    engine = await get_engine()
    result = await engine.recall(
        query=req.query,
        top_k=req.top_k,
        session_id=req.session_id,
        project=req.project,
        min_importance=req.min_importance,
        reinforce=req.reinforce,
    )
    return {
        "memories": [m.model_dump(exclude={"embedding"}) for m in result.memories],
        "scores": result.scores,
    }


@app.post("/api/ask")
async def ask_memory(req: AskRequest):
    """Ask your memory a question and get a synthesized, cited answer.

    Grounded only in stored memories; each source is returned so the UI can
    show citations. Read-only — asking does not reinforce memories. Uses an LLM
    when OPENAI_API_KEY is set, otherwise returns ranked evidence (offline)."""
    engine = await get_engine()
    return await engine.ask(
        question=req.question,
        top_k=min(max(req.top_k, 1), 20),
        session_id=req.session_id,
        project=req.project,
        min_importance=req.min_importance,
    )


@app.post("/api/memories/consolidate")
async def consolidate_memories(session_id: str = ""):
    engine = await get_engine()
    count = await engine.consolidate(session_id=session_id or None)
    return {"consolidated": count}


@app.post("/api/memories/dedupe")
async def dedupe_memories(req: DedupeRequest):
    """Find (dry_run) or remove near-duplicate memories."""
    engine = await get_engine()
    if req.dry_run:
        groups = await engine.find_duplicates(
            similarity_threshold=req.similarity_threshold, project=req.project
        )
        return {
            "dry_run": True,
            "groups": [
                [m.model_dump(exclude={"embedding"}) for m in group]
                for group in groups
            ],
            "duplicates": sum(len(g) - 1 for g in groups),
        }
    removed = await engine.dedupe(
        similarity_threshold=req.similarity_threshold, project=req.project
    )
    return {"dry_run": False, "removed": removed}


@app.post("/api/memories/consolidate-similar")
async def consolidate_similar_memories(req: ConsolidateRequest):
    """Preview (dry_run) or apply sleep-like consolidation: cluster related
    older memories and compress each cluster into one consolidated memory,
    archiving the originals inside it."""
    engine = await get_engine()
    return await engine.consolidate_memories(
        similarity_threshold=req.similarity_threshold,
        min_age_days=req.min_age_days,
        min_cluster_size=req.min_cluster_size,
        project=req.project,
        dry_run=req.dry_run,
    )


@app.post("/api/memories/evaluate-admission")
async def evaluate_admission(req: AdmissionEvalRequest):
    """Preview the admission gate's verdict for a candidate memory WITHOUT
    storing it: admit / review / redact / reject."""
    engine = await get_engine()
    return {
        "decision": await engine.evaluate_admission(
            req.content, project=req.project or None, min_length=req.min_length
        )
    }


@app.post("/api/memories/admit")
async def admit_memory(req: AdmitRequest):
    """Store a candidate memory through the admission gate: dedupe + secret
    redaction. reject/review are not stored unless force=True."""
    engine = await get_engine()
    try:
        return await engine.admit_memory(
            content=req.content,
            importance=req.importance,
            tags=req.tags,
            session_id=req.session_id,
            project=req.project,
            source=req.source or "dashboard",
            pinned=req.pinned,
            memory_type=req.memory_type,
            metadata=req.metadata,
            force=req.force,
            min_length=req.min_length,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


# ── REST Routes: Sessions ──────────────────────────────────────────


@app.post("/api/sessions")
async def create_session(req: CreateSessionRequest):
    engine = await get_engine()
    session = await engine.create_session(name=req.name, metadata=req.metadata)
    return session.model_dump()


@app.get("/api/sessions")
async def list_sessions(limit: int = 50):
    engine = await get_engine()
    sessions = await engine.list_sessions(limit=limit)
    return [s.model_dump() for s in sessions]


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    engine = await get_engine()
    session = await engine.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    return session.model_dump()


@app.patch("/api/sessions/{session_id}/end")
async def end_session(session_id: str):
    engine = await get_engine()
    session = await engine.end_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    return session.model_dump()


@app.post("/api/sessions/{session_id}/summarize")
async def summarize_session(session_id: str):
    """Distill a session's memories into one durable summary memory (LLM when
    OPENAI_API_KEY is set, deterministic extractive fallback otherwise)."""
    engine = await get_engine()
    if not await engine.get_session(session_id):
        raise HTTPException(status_code=404, detail="session not found")
    summary = await engine.summarize_session(session_id)
    if not summary:
        return {"summarized": False, "reason": "no memories in session"}
    return {"summarized": True, "summary": summary.model_dump(exclude={"embedding"})}


# ── REST Routes: Projects / Sources / Tags ──────────────────────────


@app.get("/api/projects")
async def list_projects():
    engine = await get_engine()
    return {"projects": await engine.list_projects()}


@app.get("/api/sources")
async def list_sources():
    engine = await get_engine()
    return {"sources": await engine.list_sources()}


@app.get("/api/tags")
async def list_tags():
    engine = await get_engine()
    return {"tags": await engine.list_tags()}


@app.get("/api/people")
async def list_people(limit: int = 200):
    """Distinct people across all memories (calendar attendees, email
    senders/recipients, transcript speakers), most-frequent first."""
    engine = await get_engine()
    return {"people": await engine.list_people(limit=min(max(limit, 1), 1000))}


@app.get("/api/people/{key:path}")
async def get_person(key: str):
    """A person's profile plus every memory that mentions them. ``key`` may be
    an email, a person key, or a free-text name (resolved by best match)."""
    engine = await get_engine()
    person = await engine.get_person(key)
    if person is None:
        raise HTTPException(status_code=404, detail="person not found")
    return person


@app.get("/api/timeline")
async def get_timeline(days: int = 30, project: str = ""):
    """Episodic memories grouped by day, most recent first — "what happened
    this/last week"."""
    engine = await get_engine()
    return {"timeline": await engine.timeline(days=min(max(days, 1), 365), project=project or None)}


@app.get("/api/briefing")
async def get_briefing(days: int = 7, project: str = ""):
    """Deterministic Daily Briefing — what's on today, open commitments from
    recent memories, and memories that are fading and may need review."""
    engine = await get_engine()
    return {"briefing": await engine.briefing(days=min(max(days, 1), 90), project=project or None)}


@app.get("/api/meeting-prep")
async def get_meeting_prep(query: str = "", within_days: int = 14):
    """Proactive pre-meeting brief — the next upcoming meeting (or a matched
    one), each attendee's recent context, and relevant open commitments and
    decisions. Deterministic, offline."""
    engine = await get_engine()
    return {
        "meeting_prep": await engine.meeting_prep(
            query=query or "", within_days=min(max(within_days, 1), 90)
        )
    }


@app.get("/api/organizations")
async def list_organizations(limit: int = 200):
    """Distinct organizations across all memories (people grouped by email
    domain), most-frequent first."""
    engine = await get_engine()
    return {"organizations": await engine.list_organizations(limit=min(max(limit, 1), 1000))}


@app.get("/api/organizations/{key:path}")
async def get_organization(key: str):
    """An organization's profile plus every memory that mentions someone from
    it. ``key`` may be a domain or a free-text name (resolved by best match)."""
    engine = await get_engine()
    org = await engine.get_organization(key)
    if org is None:
        raise HTTPException(status_code=404, detail="organization not found")
    return org


@app.get("/api/decisions")
async def list_decisions(days: int = 90, project: str = "", limit: int = 50):
    """Deterministic decision detection — statements like "we decided" /
    "agreed to" / "karar verdik" in recent episodic memory content."""
    engine = await get_engine()
    return {
        "decisions": await engine.list_decisions(
            days=min(max(days, 1), 365), project=project or None, limit=min(max(limit, 1), 200)
        )
    }


# ── REST Routes: Context ────────────────────────────────────────────


@app.get("/api/context")
async def get_context(session_id: str = "", project: str = "", max_tokens: int = 4000):
    engine = await get_engine()
    context = await engine.get_context(
        session_id=session_id or None,
        project=project or None,
        max_tokens=max_tokens,
    )
    return {"context": context, "chars": len(context)}


@app.post("/api/context-file")
async def generate_context_file(req: ContextFileRequest):
    """Generate a CLAUDE.md / .cursorrules style context file from memories."""
    engine = await get_engine()
    content = await engine.generate_context_file(
        project=req.project or None, style=req.style
    )
    filename = "CLAUDE.md" if req.style == "claude" else ".cursorrules"
    return {"filename": filename, "content": content}


# ── REST Routes: Stats / Health / Config ───────────────────────────


@app.get("/api/stats")
async def get_stats():
    engine = await get_engine()
    stats = await engine.get_stats()
    return stats.model_dump()


@app.get("/api/config")
async def get_config():
    """Current server configuration (for the Settings page)."""
    engine = await get_engine()
    embedder_mode = engine._embedder.mode if engine._embedder else engine._embedder_mode
    return {
        "db_path": engine.db.db_path,
        "embedder_mode": embedder_mode,
        "embedder_dimension": engine._embedder.dimension if engine._embedder else None,
        "short_term_max": engine.short_term.max_size,
        "weights": {
            "alpha": engine.scorer.w.alpha,
            "beta": engine.scorer.w.beta,
            "gamma": engine.scorer.w.gamma,
            "delta": engine.scorer.w.delta,
        },
        "decay_half_life_hours": engine.scorer.half_life_hours,
        "reinforcement_gain": engine.scorer.reinforcement_gain,
        "max_stability_hours": engine.scorer.max_stability_hours,
        "auto_summarize_sessions": engine.auto_summarize,
        # Whether anything in this install may send memory content to a remote
        # model, so the Settings page can state it plainly instead of leaving
        # users to infer it from the presence of an API key.
        "outbound": llm_policy.outbound_status(),
        "version": app.version,
    }


@app.get("/api/memories/{memory_id}/score-breakdown")
async def get_score_breakdown(memory_id: str, query: str = ""):
    """Return H(x,ψ) score breakdown for a specific memory + query pair.

    If query is empty, memory.content is used as the default query so the
    breakdown reflects self-similarity (baseline score for the memory).
    """
    engine = await get_engine()
    mem = await engine.get_memory(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="memory not found")

    # Guard: empty query falls back to memory content (produces baseline score)
    effective_query = query.strip() if query.strip() else mem.content

    # Compute similarity between query and memory
    query_embedding = await engine.embedder.embed(effective_query)
    memory_embedding = mem.embedding
    if memory_embedding is None:
        raise HTTPException(status_code=400, detail="memory has no embedding")

    # Manual cosine similarity
    import numpy as np
    q = np.asarray(query_embedding, dtype=np.float64)
    m = np.asarray(memory_embedding, dtype=np.float64)
    if q.shape != m.shape:
        raise HTTPException(
            status_code=400,
            detail="embedding dimension mismatch (embedder mode changed since storage)",
        )
    norm_q = np.linalg.norm(q)
    norm_m = np.linalg.norm(m)
    similarity = float(np.dot(q, m) / max(norm_q * norm_m, 1e-9))
    similarity = max(0.0, min(1.0, similarity))

    decay = (
        1.0
        if mem.pinned
        else engine.scorer.compute_decay(mem.accessed_at, half_life_hours=mem.stability_hours)
    )
    bd = engine.scorer.breakdown(
        similarity=similarity,
        decay_factor=decay,
        importance=mem.importance,
        frequency=mem.frequency,
    )
    score = engine.scorer.compute(
        similarity=similarity,
        decay_factor=decay,
        importance=mem.importance,
        frequency=mem.frequency,
    )

    return {
        "score": score,
        "components": {
            "similarity_penalty": bd["alpha_component"],
            "decay_penalty": bd["beta_component"],
            "importance_penalty": bd["gamma_component"],
            "frequency_penalty": bd["delta_component"],
        },
        "weights": {
            "alpha": engine.scorer.w.alpha,
            "beta": engine.scorer.w.beta,
            "gamma": engine.scorer.w.gamma,
            "delta": engine.scorer.w.delta,
        },
    }


@app.get("/api/health")
async def health():
    # Unauthenticated (exempt from the token gate) so the dashboard can learn
    # up-front whether it must ask the user for a token before any /api/* call.
    return {
        "status": "ok",
        "service": "levh",
        "auth_required": bool(_API_TOKEN),
    }


@app.post("/api/benchmark/recall")
async def benchmark_recall(embedder_mode: str = "", top_k: int = 5):
    """Run the recall-quality benchmark harness (hit@k / MRR on a labelled
    corpus) and return the metrics — powers the Settings 'Recall Quality'
    panel. Runs against an isolated temp DB/engine, never touches real data.
    """
    from server.core.benchmark import run_benchmark

    engine = await get_engine()
    mode = embedder_mode.strip() or engine.embedder.mode
    try:
        metrics = await run_benchmark(embedder_mode=mode, top_k=min(max(top_k, 1), 10))
    except Exception as e:
        logger.exception("recall benchmark failed")
        raise HTTPException(status_code=500, detail=f"Benchmark failed: {e}")
    return metrics


# ── REST Routes: Export / Import ───────────────────────────────────


@app.post("/api/memories/export")
async def export_memories(session_id: str = ""):
    engine = await get_engine()
    data = await engine.export_memories(session_id=session_id or None)
    return {"count": len(data), "data": data}


@app.post("/api/memories/import")
async def import_memories(req: ImportRequest):
    engine = await get_engine()
    return await engine.import_memories_gated(req.data)


# ── Full export (memories + entity graph + trust + conflicts) ──────


def _export_filename(ext: str) -> str:
    from datetime import datetime, timezone

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"levh-full-export-{stamp}.{ext}"


@app.get("/api/export/full.json")
async def export_full_json():
    """One-shot audit bundle: memories, entity graph, trust scores, and
    conflict candidates — the raw machine-readable record."""
    from server.core.full_export import build_full_export

    engine = await get_engine()
    export = await build_full_export(engine)
    import json as _json

    return Response(
        content=_json.dumps(export, default=str),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{_export_filename("json")}"'},
    )


@app.get("/api/export/full.sqlite")
async def export_full_sqlite():
    """Raw SQLite copy of the live database, taken via the online backup API."""
    from server.core.full_export import export_full_sqlite as export_sqlite

    engine = await get_engine()
    try:
        blob = await export_sqlite(engine)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return Response(
        content=blob,
        media_type="application/vnd.sqlite3",
        headers={"Content-Disposition": f'attachment; filename="{_export_filename("sqlite")}"'},
    )


@app.get("/api/export/full.pdf")
async def export_full_pdf():
    """Human-readable audit report (summary counts, entity/trust/conflict
    overview) rendered from the same data as the JSON export."""
    from server.core.full_export import PdfUnavailableError, build_full_export, render_full_export_pdf

    engine = await get_engine()
    export = await build_full_export(engine)
    try:
        blob = render_full_export_pdf(export)
    except PdfUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return Response(
        content=blob,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{_export_filename("pdf")}"'},
    )


# ── Backup / Restore (Faz 0 security) ───────────────────────────────


@app.post("/api/backup")
async def create_backup(req: BackupRequest):
    """Full portable snapshot (all memories + sessions) as a downloadable
    file. When ``passphrase`` is set the file is encrypted at rest
    (AES-128 via Fernet, PBKDF2-derived key); otherwise it's plain JSON.
    Returns the raw bytes with a suggested filename."""
    from datetime import datetime, timezone

    from server.core import backup as backup_mod
    from server.core.crypto import CryptoUnavailableError

    engine = await get_engine()
    snapshot = await engine.backup(app_version=app.version)
    try:
        blob = backup_mod.make_backup_blob(snapshot, passphrase=req.passphrase or None)
    except CryptoUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    encrypted = bool(req.passphrase)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    ext = "smbackup" if encrypted else "json"
    filename = f"levh-backup-{stamp}.{ext}"
    return Response(
        content=blob,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Backup-Encrypted": "1" if encrypted else "0",
            "X-Backup-Memories": str(snapshot["counts"]["memories"]),
            "X-Backup-Sessions": str(snapshot["counts"]["sessions"]),
        },
    )


@app.post("/api/restore")
async def restore_backup(req: RestoreRequest):
    """Restore from a backup file. ``content_b64`` is the base64-encoded
    backup bytes (encrypted or plain — auto-detected). ``passphrase`` is
    required only for encrypted files. ``replace=true`` first creates a local
    SQLite safety backup, then replaces the current store; the default merges."""
    import base64
    import binascii

    from server.core import backup as backup_mod
    from server.core.crypto import DecryptionError

    engine = await get_engine()
    try:
        blob = base64.b64decode(req.content_b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="content_b64 is not valid base64")

    try:
        snapshot = backup_mod.read_backup_blob(blob, passphrase=req.passphrase or None)
    except DecryptionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except backup_mod.BackupError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    result = await engine.restore(snapshot, replace=req.replace)
    return result


# ── WebSocket: live activity + RPC actions ─────────────────────────


@app.websocket("/ws/memory")
async def memory_websocket(ws: WebSocket):
    global _event_loop
    # Mirror the REST token gate: when a token is configured, the socket must
    # present it via the X-LEVH-Token header or a ?token= query param. The
    # legacy X-StackMemory-Token header remains accepted.
    if _API_TOKEN:
        supplied = (
            ws.headers.get("x-levh-token")
            or ws.headers.get("x-stackmemory-token")
            or ws.query_params.get("token")
            or ""
        )
        client_key = f"ws:{ws.client.host if ws.client else 'unknown'}"
        if not constant_time_token_matches(supplied, _API_TOKEN):
            allowed, _ = _auth_limiter.allow(client_key)
            # 1008 = policy violation; 1013 asks a compliant client to retry
            # later once the rate window has elapsed.
            await ws.close(code=1008 if allowed else 1013)
            return
        allowed, _ = _api_limiter.allow(client_key)
        if not allowed:
            await ws.close(code=1013)
            return
    # Public demo mode: only allow read-only WebSocket actions
    if _PUBLIC_DEMO:
        await ws.accept()
        engine = await get_engine()
        if _event_loop is None:
            _event_loop = asyncio.get_running_loop()
        _ws_clients.add(ws)
        try:
            while True:
                data = await ws.receive_json()
                action = data.get("action")

                if action == "recall":
                    result = await engine.recall(**data.get("params", {}))
                    await ws.send_json({
                        "type": "recalled",
                        "results": [
                            {"memory": m.model_dump(exclude={"embedding"}), "score": s}
                            for m, s in zip(result.memories, result.scores)
                        ],
                    })

                elif action == "stats":
                    stats = await engine.get_stats()
                    await ws.send_json({"type": "stats", "stats": stats.model_dump()})

                elif action == "ping":
                    await ws.send_json({"type": "pong"})

                else:
                    await ws.send_json({"type": "error", "message": f"action '{action}' forbidden in public demo mode"})
        except WebSocketDisconnect:
            pass
        finally:
            _ws_clients.discard(ws)
        return

    await ws.accept()
    engine = await get_engine()
    if _event_loop is None:
        _event_loop = asyncio.get_running_loop()
    _ws_clients.add(ws)
    try:
        while True:
            data = await ws.receive_json()
            action = data.get("action")

            if action == "store":
                params = dict(data.get("params", {}))
                params.pop("force", None)  # WebSocket is not an admin bypass surface.
                result = await engine.admit_memory(**params)
                if result["stored"]:
                    await ws.send_json({"type": "stored", "memory": result["memory"]})
                else:
                    await ws.send_json({
                        "type": "admission_blocked",
                        "decision": result["decision"],
                    })

            elif action == "recall":
                result = await engine.recall(**data.get("params", {}))
                await ws.send_json({
                    "type": "recalled",
                    "results": [
                        {"memory": m.model_dump(exclude={"embedding"}), "score": s}
                        for m, s in zip(result.memories, result.scores)
                    ],
                })

            elif action == "forget":
                success = await engine.forget(data["params"]["memory_id"])
                await ws.send_json({
                    "type": "forgotten",
                    "memory_id": data["params"]["memory_id"],
                    "success": success,
                })

            elif action == "stats":
                stats = await engine.get_stats()
                await ws.send_json({"type": "stats", "stats": stats.model_dump()})

            elif action == "ping":
                await ws.send_json({"type": "pong"})

            else:
                await ws.send_json({"type": "error", "message": f"Unknown action: {action}"})

    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.discard(ws)


# ── REST Routes: Connectors ──────────────────────────────────────


class ConnectorRequest(BaseModel):
    connector: str  # "local_files", "obsidian", "notion", "github"
    config: dict = {}
    params: dict = {}
    project: Optional[str] = None
    use_gate: bool = True


@app.post("/api/connectors/import")
async def connector_import(req: ConnectorRequest):
    """Import data from an external app via connector."""
    from server.connectors import get_connector

    try:
        conn = get_connector(req.connector)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))

    # Connect
    try:
        await conn.connect(req.config)
    except (FileNotFoundError, ValueError, ConnectionError) as e:
        raise HTTPException(status_code=400, detail=f"Connection failed: {e}")

    # Fetch
    try:
        items = await conn.fetch(**req.params)
    except Exception as e:
        await conn.disconnect()
        # Log the full error server-side, but don't echo it back — connector
        # exceptions can embed tokens/URLs from the upstream request.
        logger.exception("connector '%s' fetch failed", req.connector)
        raise HTTPException(
            status_code=502,
            detail=f"Fetch from connector '{req.connector}' failed. See server logs.",
        )

    # Legacy import surface is still admission-gated. Connector v2 adds
    # incremental cursors, but both paths share dedupe/redaction guarantees.
    engine = await get_engine()
    try:
        result = await engine.ingest_items(
            items,
            connector=req.connector,
            project=req.project,
            use_gate=True,
        )
    finally:
        await conn.disconnect()
    return result


@app.post("/api/connectors/sync")
async def connector_sync(req: ConnectorRequest):
    """Connector v2 ingest: fetch, then route items through the admission
    gate (dedupe + secret redaction), with incremental sync bookkeeping."""
    from server.connectors import get_connector

    try:
        conn = get_connector(req.connector)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))

    try:
        await conn.connect(req.config)
    except (FileNotFoundError, ValueError, ConnectionError) as e:
        raise HTTPException(status_code=400, detail=f"Connection failed: {e}")

    try:
        items = await conn.fetch(**req.params)
    except Exception:
        await conn.disconnect()
        logger.exception("connector '%s' fetch failed", req.connector)
        raise HTTPException(
            status_code=502,
            detail=f"Fetch from connector '{req.connector}' failed. See server logs.",
        )

    engine = await get_engine()
    result = await engine.ingest_items(
        items, connector=req.connector, project=req.project, use_gate=req.use_gate
    )
    await conn.disconnect()
    return result


class ConnectorUploadRequest(BaseModel):
    filename: str
    content_b64: str


# A browser never hands out the absolute path of a picked file, so the
# dashboard cannot fill in ics_path/mbox_path/transcript_path from a file
# input on its own. It uploads the bytes here instead and gets back the path
# the connector should read — the server is local, so this stays on one
# machine. Same base64-in-JSON shape as /api/restore, which keeps
# python-multipart out of the runtime dependency list.
MAX_UPLOAD_BYTES = 64 * 1024 * 1024


def _connector_upload_dir() -> Path:
    from server.core.runtime_config import resolve_runtime_config

    base = Path(resolve_runtime_config().database_path).resolve().parent
    target = base / "uploads"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _safe_upload_name(filename: str) -> str:
    """Reduce *filename* to a plain name that cannot escape the upload dir."""
    name = PurePosixPath(filename.replace("\\", "/")).name.strip()
    if not name or name in {".", ".."}:
        raise HTTPException(status_code=400, detail="filename is required")
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", name).lstrip(".")
    if not cleaned:
        raise HTTPException(status_code=400, detail="filename has no usable characters")
    return cleaned[:120]


@app.post("/api/connectors/upload")
async def connector_upload(req: ConnectorUploadRequest):
    """Store an uploaded file locally and return the path to import from."""
    import base64
    import binascii

    name = _safe_upload_name(req.filename)
    try:
        blob = base64.b64decode(req.content_b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="content_b64 is not valid base64")
    if not blob:
        raise HTTPException(status_code=400, detail="uploaded file is empty")
    if len(blob) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"file is larger than the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB upload limit",
        )

    target = _connector_upload_dir() / name
    stem, suffix = os.path.splitext(name)
    counter = 1
    while target.exists():
        target = target.with_name(f"{stem}-{counter}{suffix}")
        counter += 1
    target.write_bytes(blob)
    return {"path": str(target), "filename": target.name, "bytes": len(blob)}


@app.get("/api/connectors/sync-state")
async def connector_sync_state():
    engine = await get_engine()
    return {"sync_state": await engine.list_sync_state()}


@app.get("/api/connectors")
async def list_connectors():
    """List available connectors and their status."""
    from server.connectors import list_connectors as _list

    return {"connectors": _list()}


@app.get("/api/connectors/{name}/config")
async def get_connector_config(name: str):
    """Get required config fields for a connector."""
    from server.connectors import get_connector

    try:
        conn = get_connector(name)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return {
        "name": conn.name,
        "description": conn.description,
        "required_config_keys": conn.required_config_keys(),
        "help": conn.help_text(),
    }


# ── Entity knowledge graph (Faz 2) ──────────────────────────────────

@app.post("/api/entities/reindex")
async def reindex_entities():
    """Rebuild the persistent entity graph from every stored memory."""
    engine = await get_engine()
    return await engine.reindex_entities()


@app.get("/api/entities/stats")
async def entity_graph_stats():
    """Counts of persisted entities by type."""
    engine = await get_engine()
    return await engine.entity_graph_stats()


@app.get("/api/entities")
async def list_entities_graph(type: str = "", limit: int = 200):
    """Persisted entities (optionally filtered by type), most-mentioned first."""
    engine = await get_engine()
    return {"entities": await engine.list_entities_graph(entity_type=type or None, limit=limit)}


@app.get("/api/entities/{entity_id:path}")
async def get_entity(entity_id: str):
    """An entity's profile: the memories that mention it and the entities it
    co-occurs with. ``entity_id`` may be a full id like ``person:alice@acme.com``
    or a free-text query (resolved by best match)."""
    engine = await get_engine()
    result = await engine.get_entity(entity_id)
    if result is None:
        raise HTTPException(status_code=404, detail="entity not found")
    return result


# ── Conflict candidates (deterministic review signal) ───────────────
# Never a verdict, never auto-deletes — a human reviews each candidate.

class ConflictReviewRequest(BaseModel):
    action: str


@app.post("/api/conflicts/detect")
async def detect_conflicts():
    """Scan stored memories for conflict CANDIDATES — pairs that share an
    entity and show an opposing surface pattern. Idempotent: never resets
    already-reviewed candidates."""
    engine = await get_engine()
    return await engine.detect_conflict_candidates()


@app.get("/api/conflicts")
async def list_conflicts(status: str = "open", limit: int = 100):
    """List conflict candidates, optionally filtered by status. Pass an empty
    status to list every status."""
    engine = await get_engine()
    return {"conflicts": await engine.list_conflict_candidates(status=status or None, limit=limit)}


@app.post("/api/conflicts/{conflict_id:path}/review")
async def review_conflict(conflict_id: str, req: ConflictReviewRequest):
    """Apply a human review decision to a conflict candidate. ``conflict_id``
    may contain ``|`` so the path converter is used."""
    engine = await get_engine()
    try:
        result = await engine.review_conflict_candidate(conflict_id, req.action)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("error", "conflict not found"))
    return result


# ── MCP SSE mount ──────────────────────────────────────────────────
# FastMCP's ASGI app exposes /sse and /messages/ internally. Mounting it at
# /api/mcp makes the public stream endpoint /api/mcp/sse instead of the
# confusing /api/mcp/sse/sse double path.

from server.mcp_sse import mcp_sse

app.mount("/api/mcp", mcp_sse.sse_app())


# ── Dashboard static files (built Next.js export) ──────────────────

def _dashboard_dir() -> str | None:
    """Return the first available dashboard static export directory.

    Source checkouts serve ``frontend/out``. Built wheels serve the packaged
    copy under ``server/dashboard``. ``LEVH_DASHBOARD_DIR`` can override
    both for Docker or custom deployments.
    """
    candidates = [
        get_env("LEVH_DASHBOARD_DIR", "").strip(),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "out"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard"),
    ]
    for candidate in candidates:
        if candidate and os.path.isdir(candidate) and os.path.exists(os.path.join(candidate, "index.html")):
            return candidate
    return None


_FRONTEND_DIR = _dashboard_dir()

if _FRONTEND_DIR:
    app.mount("/", StaticFiles(directory=_FRONTEND_DIR, html=True), name="dashboard")
else:

    @app.get("/", response_class=PlainTextResponse)
    async def dashboard_placeholder():
        return (
            "LEVH API is running.\n\n"
            "The dashboard static export was not found. Build it with:\n"
            "  cd frontend && npm install && npm run build\n"
            "or set LEVH_DASHBOARD_DIR to a built dashboard directory.\n"
            "API docs: /docs"
        )
