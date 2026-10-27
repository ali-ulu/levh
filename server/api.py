"""LEVH API — FastAPI server with REST + WebSocket + MCP SSE.

Serves the dashboard frontend and exposes the memory engine via HTTP/WebSocket.
Mounts MCP SSE under /api/mcp; the stream endpoint is /api/mcp/sse.

Usage:
    uvicorn server.api:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import sys
from contextlib import asynccontextmanager

logger = logging.getLogger("levh.api")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, PlainTextResponse

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.routes import deps
from server.routes.live_broadcast import subscribe_broadcaster as _subscribe_broadcaster
from server.auth import RemoteAccessBoundaryMiddleware
from server.core import engine_provider
from server.core.env import get_env
from server.core.memory_engine import MemoryEngine
from server.core.tenancy import AuthorizationError

# ── Engine wiring ──────────────────────────────────────────────────
# The engine is dependency-injected (issue #93): ``deps.get_engine``
# resolves it from ``app.state.engine``, which ``_publish_engine`` keeps
# in sync with whichever engine the REST API and the mounted MCP SSE
# server share. Tests inject by setting ``api._engine`` as before — the
# attribute is the documented injection point and now feeds app.state.

_engine: MemoryEngine | None = None


async def _publish_engine() -> MemoryEngine:
    """Resolve, initialize, and publish the shared engine on app.state."""
    global _engine
    if _engine is None:
        _engine = engine_provider.get_engine()
    else:
        # Keep provider in sync when tests inject a custom engine here.
        engine_provider.set_engine(_engine)
    # Published before the first await on purpose: initialize() connects the DB
    # and can then fail on stored data, and the engine that already holds that
    # connection must stay reachable so the lifespan can close it.
    app.state.engine = _engine
    await _engine.initialize()  # idempotent
    _subscribe_broadcaster(_engine)
    return _engine


async def get_engine() -> MemoryEngine:
    """Resolve and publish the shared engine (test- and scaffold-facing shim)."""
    return await _publish_engine()


async def _warm_derived_state(engine: MemoryEngine) -> None:
    """Build the derived views once at startup, off the request path.

    Entity graph, conflict candidates and trust scores are marked dirty when
    the engine initializes, and the first freshness-required read then pays
    the full-corpus rebuild inline — minutes on a large store, during which
    every other request stalls behind it and the dashboard looks frozen.
    Running that rebuild here, in the background, means the views are usually
    already built by the time a human opens the dashboard. The people/orgs
    aggregates are warmed too: they are cached per data_version, and the
    first page load would otherwise pay the whole-corpus text scan.

    Failures are logged, never raised: a read still rebuilds inline when this
    warmup did not get there first.
    """
    try:
        await engine.recompute_derived_state()
        await engine.list_people(limit=1)
        await engine.list_organizations(limit=1)
        logger.info("derived-state warmup complete")
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("derived-state warmup failed; reads rebuild inline")


async def _warm_embedder(engine: MemoryEngine) -> None:
    """Load a local embedder at startup, off the request path (issue #525).

    ``Embedder._init_local`` imports torch and builds ``all-MiniLM-L6-v2``; on
    a cold machine that is ~2.5 minutes. The engine used to pay it inline on
    the first store/recall — via the ``embedder`` property — which blocked the
    event loop for the whole process and looked like a hang. Warming here means
    the model is usually loaded before the first write arrives, and the probes
    can report ``warming`` while it is not.

    Failures are logged, never raised: ``warm()`` records its own reason and
    degrades to hash, and a request that arrives first simply pays the same
    cost in a worker thread instead.
    """
    try:
        if engine.embedder.needs_warm():
            # The startup line issue #525 asks for: without it the only signal
            # that a 2.5-minute load is under way is the silence of the first
            # request, and an operator restarts the server — paying it again.
            logger.info(
                "warming local embedder in the background (requested=%s); "
                "/api/health reports embedder_state=warming until it is ready",
                engine.embedder.requested_mode,
            )
        await engine.warm_embedder()
        logger.info("embedder warmup complete")
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("embedder warmup failed; the first embed will retry it")


# ── App lifespan ─────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # ``cmd_serve`` installs this too, but the Docker image invokes uvicorn
    # directly, so the structured formatter is (re)applied here — after
    # uvicorn has configured its own logging — to cover both entry points.
    from server.core.logging import install_logging

    install_logging()
    # The engine is resolved INSIDE the try. A failing initialize() has already
    # connected the DB, and an open aiosqlite worker thread keeps the process
    # alive forever - that is how a failed startup used to hang instead of
    # exiting non-zero (orphan `levh serve` processes with no listener).
    engine: MemoryEngine | None = None
    librarian_task = None
    auto_sync_task = None
    warm_task = None
    warm_embedder_task = None
    try:
        engine = await get_engine()
        # Off the request path: see _warm_derived_state for why the first
        # freshness-required read must not be the one paying this cost.
        if engine is not None and not deps.public_demo():
            warm_task = asyncio.get_running_loop().create_task(
                _warm_derived_state(engine)
            )
        # A local embedder imports torch and builds its model on first use —
        # ~2.5 minutes cold. Doing it here means the first store/recall does not
        # stall, and /api/health reports `embedder_state: warming` while it
        # runs, so the wait is visible instead of looking like a hang (#525).
        # A no-op for hash/openai/ollama, so this costs nothing on the default.
        if engine is not None and not deps.public_demo():
            warm_embedder_task = asyncio.get_running_loop().create_task(
                _warm_embedder(engine)
            )
        # Librarian bekçi ajanı — sunucu açılınca başlar, kapanırken durur.
        # Public demo'da çalışmaz: orada her yazma zaten reddedilir ve bekçinin
        # kendi bulguları demo korpusunu kirletir.
        if not deps.public_demo() and get_env("LEVH_LIBRARIAN", "1").strip().lower() not in {
            "0", "false", "off",
        }:
            from server.core import librarian
            librarian_task = librarian.start_background()
        # Otomatik besleme — yalnızca config dosyası isterse: varsayılan
        # kapalı bir kurulum eskisi gibi davranır (pull-on-demand).
        if engine is not None and not deps.public_demo():
            from server.core import auto_sync
            auto_sync_task = auto_sync.start_background(engine)
        yield
    finally:
        if warm_task:
            warm_task.cancel()
            # Await it like the librarian: an un-awaited cancel races the
            # engine's own shutdown below.
            with contextlib.suppress(asyncio.CancelledError):
                await warm_task
        if warm_embedder_task:
            # The load runs in a worker thread, so cancelling stops waiting on
            # it but cannot interrupt the import; the engine's shutdown below
            # closes whatever the embedder already opened.
            warm_embedder_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await warm_embedder_task
        if auto_sync_task:
            auto_sync_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await auto_sync_task
        if librarian_task:
            librarian_task.cancel()
            # Cancel only *requests* cancellation. Without awaiting, shutdown
            # races the task: the engine can close under a scan still mid-write,
            # and Python reports it as "Task was destroyed but it is pending".
            with contextlib.suppress(asyncio.CancelledError):
                await librarian_task
        # ``_publish_engine`` publishes before initializing, so even a startup
        # that failed mid-way has an owner and its DB is closed here.
        target = engine if engine is not None else getattr(_app.state, "engine", None)
        if target is not None:
            await target.shutdown()


# ── FastAPI app ─────────────────────────────────────────────────────

app = FastAPI(
    title="LEVH API",
    version="2.36.0",
    description="Local-first memory layer for AI agents and humans",
    lifespan=lifespan,
)

@app.exception_handler(AuthorizationError)
async def _authorization_error_handler(_request, _exc: AuthorizationError):
    """Project storage authorization failures as one non-leaky HTTP verdict."""
    return JSONResponse(status_code=403, content={"detail": "forbidden"})


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
# Live token callable: binding the value at import time would freeze the
# boundary to whatever LEVH_TOKEN held when this module was first imported
# (issue #132) — the same reason server/middleware.py re-reads per request.
app.add_middleware(RemoteAccessBoundaryMiddleware, token=deps.api_token)


# ── Middleware ──────────────────────────────────────────────────────
# The token gate and the public-demo boundary live in server.middleware.

from server import middleware

middleware.install(app)


# ── Routers ─────────────────────────────────────────────────────────
# Order matters. Literal paths must be registered before the router that
# owns "/api/memories/{memory_id}", or the path parameter swallows them
# (GET /api/memories/fading would resolve as memory_id="fading").

from server.routes import (
    agents,
    attachments,
    conflicts,
    connectors,
    context,
    data_transfer,
    entities,
    findings,
    guard,
    knowledge,
    live,
    memories,
    memory_item,
    onboarding,
    sessions,
    system,
    team_memory,
    wiki,
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
app.include_router(wiki.router)
app.include_router(guard.router)
app.include_router(conflicts.router)
app.include_router(team_memory.router)
app.include_router(findings.router)
app.include_router(live.router)

# ── Librarian bekçi ajanı ──────────────────────────────────────────
from server.routes.librarian import router as librarian_router

app.include_router(librarian_router)

# ── Opengeni asistan sohbeti ───────────────────────────────────────
# The embedded memory-assistant proxy: the browser's chat UI talks only to
# /api/opengeni/* here, and this process holds the organization key.
from server.routes.opengeni import router as opengeni_router

app.include_router(opengeni_router)


# ── Sürümlemeli API yüzeyi (/api/v1) ───────────────────────────────
# Ayrıntı ve gerekçe: server/api_versioning.py. Eski (/api/*) yollar geriye
# dönük uyumluluk için çalışmaya devam eder; yayımlanan sözleşme /api/v1'dir.

from server.api_versioning import install_versioned_surface

install_versioned_surface(app)


# ── Librarian chat widget enjeksiyonu ──────────────────────────────
# Dashboard'un her HTML sayfasına, </body>'den önce widget script'i eklenir.
#
# Yeniden yazılan gövde, orijinalin header'larını olduğu gibi devralamaz:
# content-length artık yanlıştır, content-encoding gövdenin sıkıştırılmış
# olduğunu söyler (değildir) ve etag/content-digest artık başka bir gövdeyi
# tarifler. Üçünü de bırakmak, tarayıcının çözemediği bozuk bir yanıt üretir —
# ve hata görünür bir hata değil, sessiz bir bozulma olarak çıkar.

from starlette.responses import Response as _StarletteResponse

# Yeniden yazılan gövdeyi artık tarif etmeyen header'lar.
_STALE_AFTER_REWRITE = ("content-length", "content-encoding", "etag", "content-digest")


_WIDGET_TAG = '<script src="/librarian.js"></script>'


@app.middleware("http")
async def _inject_librarian_widget(request, call_next):
    response = await call_next(request)
    content_type = response.headers.get("content-type", "")
    if "text/html" not in content_type or response.status_code != 200:
        return response
    # Sıkıştırılmış gövdeyi burada çözmüyoruz; dokunmadan geçirmek, yanlış
    # header'la bozuk metin döndürmekten iyidir.
    if response.headers.get("content-encoding"):
        return response
    if not hasattr(response, "body_iterator"):
        return response

    body = b""
    async for chunk in response.body_iterator:
        body += chunk

    headers = {
        k: v for k, v in response.headers.items()
        if k.lower() not in _STALE_AFTER_REWRITE
    }
    try:
        text = body.decode(response.charset or "utf-8")
    except UnicodeDecodeError:
        return _StarletteResponse(
            body, status_code=response.status_code,
            headers=headers, media_type=content_type,
        )

    if "</body>" in text and _WIDGET_TAG not in text:
        text = text.replace("</body>", _WIDGET_TAG + "</body>", 1)
    return _StarletteResponse(
        text, status_code=response.status_code,
        headers=headers, media_type="text/html; charset=utf-8",
    )


# ── MCP SSE mount ──────────────────────────────────────────────────
# FastMCP's ASGI app exposes /sse and /messages/ internally. Mounting it at
# /api/mcp makes the public stream endpoint /api/mcp/sse instead of the
# confusing /api/mcp/sse/sse double path.

from server.mcp_sse import mcp_sse

app.mount("/api/mcp", mcp_sse.sse_app())


# ── Dashboard static files (built Next.js export) ──────────────────

def _dashboard_candidates() -> list[str]:
    """Dashboard export directories in preference order (override first)."""
    return [
        get_env("LEVH_DASHBOARD_DIR", "").strip(),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "out"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard"),
    ]


_CHUNK_REF = re.compile(r"""["'(/](_next/static/[^"'()\s?#]+\.(?:js|css))""")


def _missing_dashboard_assets(candidate: str) -> list[str]:
    """Assets an export's HTML references but does not ship.

    ``frontend/out`` is committed as HTML shells while its content-hashed
    ``_next`` bundles are gitignored, so a checkout that once built it keeps a
    *stale* ``_next`` beside *newer* committed HTML. ``_next/static`` still
    exists, but the hashes the HTML asks for are gone: ``/`` answers 200 while
    every referenced chunk 404s and React never hydrates — the dashboard renders
    "Something went wrong" on the routes whose chunk is missing.

    Scanning every ``*.html`` is what tells that apart from a usable export. The
    check is bounded to the export's own pages (not the whole tree) and returns
    at most one missing asset, which is all a caller needs to reject a candidate.
    """
    for dirpath, dirs, files in os.walk(candidate):
        # Bundles are never HTML; descending into them only slows the walk.
        dirs[:] = [d for d in dirs if d != "_next"]
        for name in files:
            if not name.endswith(".html"):
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path, encoding="utf-8", errors="replace") as handle:
                    markup = handle.read()
            except OSError:
                continue
            for match in _CHUNK_REF.finditer(markup):
                asset = os.path.join(candidate, *match.group(1).split("/"))
                if not os.path.isfile(asset):
                    return [os.path.relpath(asset, candidate)]
    return []


def _dashboard_dir() -> str | None:
    """Return the first available dashboard static export directory.

    Source checkouts serve ``frontend/out``. Built wheels serve the packaged
    copy under ``server/dashboard``. ``LEVH_DASHBOARD_DIR`` can override
    both for Docker or custom deployments.

    ``index.html`` alone is not enough to call an export servable. ``frontend/out``
    is committed as HTML shells while its content-hashed ``_next`` bundles are
    gitignored and excluded from the release commits, so a checkout can hold an
    ``index.html`` that points at bundles no process can serve: ``/`` answers 200
    while every chunk 404s and React never hydrates. Checking that the referenced
    bundles are actually present — not merely that an ``_next/static`` directory
    exists — keeps such an export from shadowing the complete packaged copy.

    A candidate that carries an ``index.html`` is remembered as a last resort, so
    an intentionally bundle-less directory stays servable and this never returns
    ``None`` where a directory could still be mounted. A candidate with no
    ``index.html`` at all is rejected outright.
    """
    incomplete: str | None = None
    for candidate in _dashboard_candidates():
        if not candidate or not os.path.isdir(candidate):
            continue
        if not os.path.exists(os.path.join(candidate, "index.html")):
            continue
        if incomplete is None:
            incomplete = candidate
        # Next writes its client bundles under `_next/static`; `_next` alone can
        # exist as an empty directory, and a stale one can exist beside newer
        # HTML. Only a candidate that ships every referenced bundle is complete.
        if os.path.isdir(os.path.join(candidate, "_next", "static")) and not _missing_dashboard_assets(candidate):
            return candidate
    return incomplete


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
