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
import sys
from contextlib import asynccontextmanager

logger = logging.getLogger("levh.api")

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import PlainTextResponse

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.routes import deps  # noqa: E402
from server.auth import (
    AUTH_RATE_LIMIT,
    AUTH_RATE_LIMIT_WINDOW_SECONDS,
    RemoteAccessBoundaryMiddleware,
    shared_auth_limiter,
)
from server.core import engine_provider
from server.core.env import get_env
from server.core.memory_engine import MemoryEngine
from server.core.rate_limit import SlidingWindowRateLimiter

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
    # Librarian bekçi ajanı — sunucu açılınca başlar, kapanırken durur.
    librarian_task = None
    if get_env("LEVH_LIBRARIAN", "1").strip().lower() not in {"0", "false", "off"}:
        from server.core import librarian
        librarian_task = librarian.start_background()
    try:
        yield
    finally:
        if librarian_task:
            librarian_task.cancel()
        await engine.shutdown()


# ── FastAPI app ─────────────────────────────────────────────────────

app = FastAPI(
    title="LEVH API",
    version="2.29.0",
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
app.add_middleware(RemoteAccessBoundaryMiddleware, token=deps.api_token())
_AUTH_RATE_LIMIT = AUTH_RATE_LIMIT
try:
    _API_RATE_LIMIT = int(get_env("LEVH_API_RATE_LIMIT", "120"))
except ValueError:
    _API_RATE_LIMIT = 120
_RATE_LIMIT_WINDOW = AUTH_RATE_LIMIT_WINDOW_SECONDS

_auth_limiter = shared_auth_limiter
_api_limiter = SlidingWindowRateLimiter(_API_RATE_LIMIT, _RATE_LIMIT_WINDOW)


# ── Middleware ──────────────────────────────────────────────────────
# The token gate and the public-demo boundary live in server.middleware.

from server import middleware  # noqa: E402

middleware.install(app)


# ── Routers ─────────────────────────────────────────────────────────
# Order matters. Literal paths must be registered before the router that
# owns "/api/memories/{memory_id}", or the path parameter swallows them
# (GET /api/memories/fading would resolve as memory_id="fading").

from server.routes import (  # noqa: E402
    agents,
    attachments,
    conflicts,
    connectors,
    context,
    data_transfer,
    entities,
    guard,
    knowledge,
    live,
    memories,
    memory_item,
    onboarding,
    sessions,
    system,
)

app.include_router(memories.router)
app.include_router(attachments.router)
app.include_router(memory_item.router)
app.include_router(sessions.router)
app.include_router(agents.router)
app.include_router(onboarding.router)
app.include_router(knowledge.router)
app.include_router(context.router)
app.include_router(system.router)
app.include_router(data_transfer.router)
app.include_router(connectors.router)
app.include_router(entities.router)
app.include_router(guard.router)
app.include_router(conflicts.router)
app.include_router(live.router)

# ── Librarian bekçi ajanı ──────────────────────────────────────────
from server.routes.librarian import router as librarian_router  # noqa: E402

app.include_router(librarian_router)


# ── Librarian chat widget enjeksiyonu ──────────────────────────────
# Dashboard (Next.js export) yeniden derlemeden: her HTML sayfasının
# </body>'sinden önce widget script'i eklenir. Sağ altta sohbet düğmesi.

from starlette.responses import Response as _StarletteResponse  # noqa: E402


@app.middleware("http")
async def _inject_librarian_widget(request, call_next):
    response = await call_next(request)
    content_type = response.headers.get("content-type", "")
    if "text/html" not in content_type:
        return response
    body = b""
    async for chunk in response.body_iterator:
        body += chunk
    try:
        text = body.decode(response.charset or "utf-8")
        if "</body>" in text and "/librarian.js" not in text:
            text = text.replace(
                "</body>", '<script src="/librarian.js"></script></body>', 1
            )
        headers = dict(response.headers)
        headers.pop("content-length", None)
        response = _StarletteResponse(
            text, status_code=response.status_code,
            headers=headers, media_type="text/html; charset=utf-8",
        )
    except UnicodeDecodeError:
        return _StarletteResponse(
            body, status_code=response.status_code,
            headers=dict(response.headers),
        )
    return response


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


class DashboardStaticFiles(StaticFiles):
    """StaticFiles that tells the browser which assets are safe to keep.

    Next.js emits content-hashed bundles under ``_next/static``: the filename
    changes whenever the contents do, so they can be cached forever. The HTML
    documents that *point* at those bundles must never be cached blindly —
    without an explicit header a browser applies heuristic freshness (roughly
    10% of the Last-Modified age) and will happily reuse a stale document for
    hours. After an upgrade that document references bundle hashes that no
    longer exist on disk, every one of them 404s, React cannot hydrate, and the
    app dies with "a client-side exception has occurred" until the user knows
    to hard-reload. ``no-cache`` still allows a cheap ETag revalidation (304).
    """

    IMMUTABLE_PREFIX = "_next/static"

    def file_response(self, full_path, stat_result, scope, status_code=200):
        response = super().file_response(full_path, stat_result, scope, status_code)
        path = scope.get("path", "").lstrip("/")
        if path.startswith(self.IMMUTABLE_PREFIX):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"
        return response


_FRONTEND_DIR = _dashboard_dir()

if _FRONTEND_DIR:
    app.mount("/", DashboardStaticFiles(directory=_FRONTEND_DIR, html=True), name="dashboard")
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
