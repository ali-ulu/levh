"""Shared token-authentication primitives for LEVH HTTP transports."""

from __future__ import annotations

import ipaddress
import logging
import secrets
from collections.abc import Iterable
from typing import Any
from urllib.parse import parse_qs

from starlette.responses import JSONResponse

from server.core.env import get_env
from server.core.rate_limit import SlidingWindowRateLimiter


PRIMARY_TOKEN_HEADER = b"x-levh-token"
LEGACY_TOKEN_HEADER = b"x-stackmemory-token"
ALLOW_REMOTE_WITHOUT_TOKEN_ENV = "LEVH_ALLOW_REMOTE_WITHOUT_TOKEN"

logger = logging.getLogger("levh.auth")

try:
    AUTH_RATE_LIMIT = int(get_env("LEVH_AUTH_RATE_LIMIT", "10"))
except ValueError:
    AUTH_RATE_LIMIT = 10
try:
    AUTH_RATE_LIMIT_WINDOW_SECONDS = float(
        get_env("LEVH_RATE_LIMIT_WINDOW_SECONDS", "60")
    )
except ValueError:
    AUTH_RATE_LIMIT_WINDOW_SECONDS = 60.0

shared_auth_limiter = SlidingWindowRateLimiter(
    AUTH_RATE_LIMIT,
    AUTH_RATE_LIMIT_WINDOW_SECONDS,
)
remote_rejection_log_limiter = SlidingWindowRateLimiter(
    1,
    AUTH_RATE_LIMIT_WINDOW_SECONDS,
)


def _token_bytes(value: str | bytes | None) -> bytes:
    """Normalize missing, text, and raw ASGI header values for comparison."""
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    return value.encode("utf-8")


def constant_time_token_matches(
    supplied: str | bytes | None,
    expected: str | bytes | None,
) -> bool:
    """Compare tokens without failing on missing or non-ASCII input."""
    return secrets.compare_digest(_token_bytes(supplied), _token_bytes(expected))


def token_from_asgi_headers(
    headers: Iterable[tuple[bytes, bytes]],
) -> bytes | None:
    """Return the primary token header, falling back only when it is absent."""
    primary: bytes | None = None
    legacy: bytes | None = None
    primary_found = False
    legacy_found = False
    for name, value in headers:
        lowered = name.lower()
        if lowered == PRIMARY_TOKEN_HEADER and not primary_found:
            primary = value
            primary_found = True
        elif lowered == LEGACY_TOKEN_HEADER and not legacy_found:
            legacy = value
            legacy_found = True
    if primary_found:
        return primary
    if legacy_found:
        return legacy
    return None


def token_from_query_string(query_string: bytes) -> bytes | None:
    """Read one percent-decoded ``token`` query value as raw UTF-8 bytes."""
    values = parse_qs(query_string, keep_blank_values=True).get(b"token", [])
    return values[0] if len(values) == 1 else None


def _is_loopback(host: str | None) -> bool:
    """Return whether an ASGI peer host is a loopback address."""
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if address.is_loopback:
        return True
    mapped = getattr(address, "ipv4_mapped", None)
    return bool(mapped and mapped.is_loopback)


def remote_without_token_allowed() -> bool:
    """Return whether an operator explicitly owns the external network boundary."""
    return str(get_env(ALLOW_REMOTE_WITHOUT_TOKEN_ENV, "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


class RemoteAccessBoundaryMiddleware:
    """Keep tokenless HTTP transports loopback-only unless explicitly overridden."""

    def __init__(
        self,
        app: Any,
        token: str | bytes | None,
        warning_limiter: SlidingWindowRateLimiter = remote_rejection_log_limiter,
    ) -> None:
        self.app = app
        self.token = _token_bytes(token)
        self.warning_limiter = warning_limiter
        self._override_warning_logged = False

    def _warn_if_override_enabled(self) -> None:
        if (
            not self.token
            and remote_without_token_allowed()
            and not self._override_warning_logged
        ):
            logger.warning(
                "%s=true: unauthenticated remote access is enabled; "
                "ensure a trusted network boundary prevents public exposure",
                ALLOW_REMOTE_WITHOUT_TOKEN_ENV,
            )
            self._override_warning_logged = True

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        scope_type = scope.get("type")
        self._warn_if_override_enabled()
        if scope_type == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope_type not in {"http", "websocket"} or self.token:
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        client_host = str(client[0]) if client else None
        if remote_without_token_allowed():
            await self.app(scope, receive, send)
            return
        if _is_loopback(client_host):
            await self.app(scope, receive, send)
            return

        log_key = client_host or "unknown"
        should_log, _ = self.warning_limiter.allow(log_key)
        if should_log:
            logger.warning(
                "rejected remote request from %s: set LEVH_TOKEN, or "
                "%s=true if the network boundary is handled elsewhere",
                log_key,
                ALLOW_REMOTE_WITHOUT_TOKEN_ENV,
            )
        if scope_type == "websocket":
            await send(
                {
                    "type": "websocket.close",
                    "code": 1008,
                    "reason": "remote access requires LEVH_TOKEN",
                }
            )
            return
        response = JSONResponse(
            {"detail": "remote access requires LEVH_TOKEN"},
            status_code=401,
        )
        await response(scope, receive, send)


class ConfiguredTokenAuthMiddleware:
    """Enforce the remote boundary and any configured transport token."""

    def __init__(
        self,
        app: Any,
        token: str | bytes | None,
        limiter: SlidingWindowRateLimiter = shared_auth_limiter,
    ) -> None:
        self.app = RemoteAccessBoundaryMiddleware(app, token)
        self.token = _token_bytes(token)
        self.limiter = limiter

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        scope_type = scope.get("type")
        if scope_type not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        client_host = str(client[0]) if client else None
        if not self.token:
            await self.app(scope, receive, send)
            return

        # A supplied header is authoritative, even when empty or wrong. Query
        # fallback exists only for browser EventSource, which cannot set custom
        # headers; it must never turn a failed header attempt into success.
        supplied = token_from_asgi_headers(scope.get("headers", ()))
        if supplied is None:
            supplied = token_from_query_string(scope.get("query_string", b""))
        if constant_time_token_matches(supplied, self.token):
            await self.app(scope, receive, send)
            return

        allowed, retry_after = self.limiter.allow(client_host or "unknown")
        if not allowed:
            if scope["type"] == "websocket":
                await send(
                    {
                        "type": "websocket.close",
                        "code": 1013,
                        "reason": "too many authentication attempts",
                    }
                )
                return
            response = JSONResponse(
                {"detail": "too many authentication attempts"},
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )
            await response(scope, receive, send)
            return

        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008, "reason": "unauthorized"})
            return

        response = JSONResponse({"detail": "unauthorized"}, status_code=401)
        await response(scope, receive, send)
