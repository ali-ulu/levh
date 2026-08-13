"""Shared dependencies for the route modules.

The engine lifecycle deliberately stays in ``server.api``: the module globals
``_engine`` / ``_initialized`` are the documented test-injection point, and a
structural split is the wrong moment to move ownership of them. ``get_engine``
here delegates to it with a call-time import, which keeps a single source of
truth and avoids the circular import that a module-level one would create
(``api`` imports the routers, the routers import this).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from server.auth import (
    AUTH_RATE_LIMIT,
    AUTH_RATE_LIMIT_WINDOW_SECONDS,
    constant_time_token_matches,
    shared_auth_limiter,
)
from server.core.env import get_env
from server.core.rate_limit import SlidingWindowRateLimiter

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastapi import WebSocket

    from server.core.memory_engine import MemoryEngine


async def get_engine() -> "MemoryEngine":
    """Return the shared, initialized engine."""
    from server import api

    return await api.get_engine()


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
# Owned by server.api, which subscribes the broadcaster to engine events.


def ws_clients() -> set["WebSocket"]:
    from server import api

    return api._ws_clients


def set_event_loop_if_unset() -> None:
    """Remember the loop the WebSocket route is running on, once."""
    from server import api

    if api._event_loop is None:
        api._event_loop = asyncio.get_running_loop()


# ── Shared configuration ────────────────────────────────────────────
# The app version, the token gate and the rate limiters live here rather
# than in server.api so a router can reach them without importing the app
# module it is itself imported by.

logger = logging.getLogger("levh.api")

APP_VERSION = "2.28.0"

def api_token() -> str:
    """The shared-secret gate, or "" when the server is open.

    A function for the same reason as public_demo(): it decides a security
    boundary, and a constant frozen at import would leave a reloaded caller
    and a stale importer disagreeing about whether a token is required.
    """
    return get_env("LEVH_TOKEN", "").strip()

try:
    API_RATE_LIMIT = int(get_env("LEVH_API_RATE_LIMIT", "120"))
except ValueError:
    API_RATE_LIMIT = 120

RATE_LIMIT_WINDOW = AUTH_RATE_LIMIT_WINDOW_SECONDS
auth_limiter = shared_auth_limiter
api_limiter = SlidingWindowRateLimiter(API_RATE_LIMIT, RATE_LIMIT_WINDOW)

__all__ = [
    "API_RATE_LIMIT",
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
