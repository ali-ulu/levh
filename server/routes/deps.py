"""Shared dependencies for the route modules.

Engine access is dependency-injected (issue #93): routes declare
``engine: MemoryEngine = Depends(get_engine)`` instead of reaching for a
process-wide singleton, so who serves a request is decided by the app rather
than by an import. WebSocket handlers use ``get_engine_for(ws)`` — FastAPI
cannot inject ``Request`` into a websocket-scoped dependency, so the socket
passes itself.

One owner, one view: ``server.api._engine`` owns the engine (the app
publishes it at startup; it is also the documented harness injection point)
and ``app.state.engine`` is the app-visible view of it.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import Request

from server.auth import (
    AUTH_RATE_LIMIT,
    AUTH_RATE_LIMIT_WINDOW_SECONDS,
    constant_time_token_matches,
    shared_auth_limiter,
)
from server.core.env import get_env
from server.core.rate_limit import SlidingWindowRateLimiter
from server.entrypoint import levh_version

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastapi import Request, WebSocket

    from server.core.memory_engine import MemoryEngine


async def get_engine(request: Request) -> "MemoryEngine":
    """FastAPI dependency: the app's shared, initialized engine (HTTP routes)."""
    return await get_engine_for(request)


async def get_engine_for(conn: "Request | WebSocket") -> "MemoryEngine":
    """Resolve the shared, initialized engine from an ASGI connection.

    Works for both ``Request`` (HTTP) and ``WebSocket`` connections — both
    expose ``.app``.

    Resolution: the engine the app owns (``server.api._engine``, which the
    lifespan publishes and tests may swap) wins; otherwise the process-wide
    provider engine is used, creating it from env config if this is the first
    caller. Either way the result is written back to the provider and to
    ``app.state.engine``, so every transport — the ingest workers, the
    librarian scans, the mounted MCP SSE app — operates on the same database
    as the request that triggered it. ``app.state`` is deliberately *not* read
    back: a view left behind by an earlier app instance must never outrank a
    freshly installed engine.
    """
    from server import api
    from server.core import engine_provider

    engine = api._engine
    if engine is not None:
        # Keep the provider aligned when a harness injected the engine here.
        engine_provider.set_engine(engine)
    else:
        engine = engine_provider.get_engine()
    conn.app.state.engine = engine
    await engine.initialize()  # idempotent
    from server.routes.live_broadcast import subscribe_broadcaster

    subscribe_broadcaster(engine)  # idempotent per engine instance
    return engine


# ── Public demo mode ────────────────────────────────────────────────


def public_demo() -> bool:
    """Whether this process is serving a read-only public demo.

    Read on each call rather than frozen at import. The flag decides a
    security boundary, and a module-level constant would be captured by
    whichever module imported first — leaving a reloaded caller and a
    stale importer disagreeing about whether writes are allowed.
    """
    return get_env("LEVH_PUBLIC_DEMO", "").strip().lower() == "true"


# ── Live WebSocket registry ─────────────────────────────────────────
# Owned by server.routes.live_broadcast; server.api subscribes the
# broadcaster to engine events at startup.

from server.routes.live_broadcast import set_event_loop_if_unset, ws_clients


# ── Shared configuration ────────────────────────────────────────────
# The app version, the token gate and the rate limiters live here rather
# than in server.api so a router can reach them without importing the app
# module it is itself imported by.

logger = logging.getLogger("levh.api")

# Derived, never a literal: server/api.py already carries the canonical
# version that scripts/release.py rewrites, and a second copy here is exactly
# the drift #46 hit — it silently stayed at 2.28.0 through a 2.29.0 bump.
APP_VERSION = levh_version()

def api_token() -> str:
    """The shared-secret gate, or "" when the server is open.

    A function for the same reason as public_demo(): it decides a security
    boundary, and a constant frozen at import would leave a reloaded caller
    and a stale importer disagreeing about whether a token is required.
    """
    return get_env("LEVH_TOKEN", "").strip()


def api_docs_enabled() -> bool:
    """Whether the interactive API docs surface is served.

    ``/docs``, ``/openapi.json`` and ``/redoc`` are a development convenience
    and, at the same time, an itemised map of every route. A browser cannot
    attach ``X-LEVH-Token`` while loading ``/docs`` itself, so serving it next
    to a token gate would hand an anonymous caller the whole API surface.

    The docs therefore follow the token: served while the server is open (the
    zero-config local case), withheld once ``LEVH_TOKEN`` is set, and restored
    deliberately with ``LEVH_ENABLE_API_DOCS=true`` when the operator accepts
    the exposure on a trusted network.

    A function, not a constant, for the same reason as :func:`api_token` — the
    boundary must track the environment, not the import.
    """
    if get_env("LEVH_ENABLE_API_DOCS", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    return not api_token()


try:
    API_RATE_LIMIT = int(get_env("LEVH_API_RATE_LIMIT", "120"))
except ValueError:
    API_RATE_LIMIT = 120

RATE_LIMIT_WINDOW = AUTH_RATE_LIMIT_WINDOW_SECONDS
auth_limiter = shared_auth_limiter
api_limiter = SlidingWindowRateLimiter(API_RATE_LIMIT, RATE_LIMIT_WINDOW)

__all__ = [
    "API_RATE_LIMIT",
    "api_docs_enabled",
    "api_token",
    "APP_VERSION",
    "AUTH_RATE_LIMIT",
    "public_demo",
    "RATE_LIMIT_WINDOW",
    "api_limiter",
    "auth_limiter",
    "constant_time_token_matches",
    "get_engine",
    "logger",
    "set_event_loop_if_unset",
    "ws_clients",
]
