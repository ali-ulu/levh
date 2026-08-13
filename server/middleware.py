"""HTTP middleware: the token gate and the public-demo boundary.

Both decide who may reach ``/api/*`` at all, so they live together and apart
from the routes they protect. ``install(app)`` attaches them; ``server.api``
calls it once while building the app.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from server.routes import deps
from server.routes.deps import constant_time_token_matches, public_demo

# The limiters are reached through the module (deps.auth_limiter) rather than
# imported by value: they are swapped at runtime by tests, and a by-value
# import would bind this module to the originals forever.

# Handing over the whole database in one request is not a read a demo visitor
# should get, even though it arrives as a GET.
PUBLIC_DEMO_BLOCKED_PATHS = {
    "/api/export/full.json",
    "/api/export/full.sqlite",
    "/api/export/full.pdf",
}

# Recall reads memory but has to POST to carry its query, so the blanket
# method rule would kill search on the public demo — while the WebSocket path
# deliberately allows the same action. It is let through here and its one side
# effect (reinforcement) is neutralized in the endpoint itself.
PUBLIC_DEMO_ALLOWED_POSTS = {"/api/memories/recall"}


def _client_key(request: Request) -> str:
    # Do not trust X-Forwarded-For by default; deployments behind a trusted
    # reverse proxy should terminate/rate-limit there as well.
    return request.client.host if request.client else "unknown"


def _guarded(request: Request) -> bool:
    """Whether this request is subject to the /api gates."""
    return request.url.path.startswith("/api/") and request.url.path != "/api/health"


def install(app: FastAPI) -> None:
    """Attach the middleware to *app*.

    Registration order is reversed at request time, so the demo guard is added
    last to keep it running in the same position it had when both lived in
    server.api: token first, then the demo boundary.
    """

    @app.middleware("http")
    async def require_token(request: Request, call_next):
        token = deps.api_token()
        if token and _guarded(request):
            client_key = _client_key(request)
            supplied = (
                request.headers.get("X-LEVH-Token")
                or request.headers.get("X-StackMemory-Token", "")
            )
            if not constant_time_token_matches(supplied, token):
                allowed, retry_after = deps.auth_limiter.allow(client_key)
                if not allowed:
                    return JSONResponse(
                        {"detail": "too many authentication attempts"},
                        status_code=429,
                        headers={"Retry-After": str(retry_after)},
                    )
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
            allowed, retry_after = deps.api_limiter.allow(client_key)
            if not allowed:
                return JSONResponse(
                    {"detail": "rate limit exceeded"},
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )
        return await call_next(request)

    @app.middleware("http")
    async def public_demo_guard(request: Request, call_next):
        if public_demo() and _guarded(request):
            if request.method in ("GET", "HEAD", "OPTIONS"):
                if request.url.path in PUBLIC_DEMO_BLOCKED_PATHS:
                    return JSONResponse(
                        {"detail": "forbidden in public demo mode"},
                        status_code=403,
                    )
                return await call_next(request)
            if request.method == "POST" and request.url.path in PUBLIC_DEMO_ALLOWED_POSTS:
                return await call_next(request)
            return JSONResponse(
                {"detail": "forbidden in public demo mode: mutating endpoint"},
                status_code=403,
            )
        return await call_next(request)
