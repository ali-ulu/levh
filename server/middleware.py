"""HTTP middleware: the token gate and the public-demo boundary.

Both decide who may reach the guarded API surface at all — the ``/api/*``
routes plus the generated docs the app serves at the root — so they live
together and apart from the routes they protect. ``install(app)`` attaches
them; ``server.api`` calls it once while building the app.
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


# FastAPI's generated docs surface. ``/docs`` and ``/redoc`` load without
# ``X-LEVH-Token`` (a browser cannot attach it to the document request), so
# once a token gate is in force these paths would expose the full route map to
# an anonymous caller. ``deps.api_docs_enabled()`` decides whether they are
# served at all; this set is what the gate below refuses when they are not.
# ``/docs/oauth2-redirect`` is in the set even though Swagger UI only reaches
# it after a successful interactive login: it is part of the generated surface
# #144 closed, and leaving it outside would keep the route map's existence
# anonymously confirmable while every sibling path 404s.
_DOCS_PATHS = {"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}


def _guarded(request: Request) -> bool:
    """Whether this request is subject to the /api gates."""
    return request.url.path.startswith("/api/") and request.url.path != "/api/health"


def _docs_exposed(request: Request) -> bool:
    """A docs path that must not be served while the token gate is active.

    Checked against the *token*, not merely against ``_guarded``: a docs
    request never carries the header, so letting it through the token gate
    would always fail 401 and leave the operator without the documented
    ``/docs`` URL. Withholding it is the honest answer, and
    ``LEVH_ENABLE_API_DOCS=true`` is the documented way back.

    Trailing-slash tolerant, like ``_guarded``: the docs routes redirect
    ``/docs/`` to ``/docs``, and a redirect answered outside the gate would
    still confirm the surface exists.
    """
    return request.url.path.rstrip("/") in _DOCS_PATHS and not deps.api_docs_enabled()


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

    @app.middleware("http")
    async def api_docs_guard(request: Request, call_next):
        """Withhold /docs, /redoc and /openapi.json while a token gate is on.

        Registered last so it is the outermost layer and refunds before the
        token gate's 401: a docs request cannot carry ``X-LEVH-Token``, so the
        choice is between a 404 (the surface is not served) and a 401 the
        operator cannot satisfy. 404 states the truth and keeps the refusal
        from advertising which paths exist.
        """
        if _docs_exposed(request):
            return JSONResponse({"detail": "not found"}, status_code=404)
        return await call_next(request)
