"""HTTP middleware: the token gate and the public-demo boundary.

Both decide who may reach the guarded API surface at all — the ``/api/*``
routes plus the generated docs the app serves at the root — so they live
together and apart from the routes they protect. ``install(app)`` attaches
them; ``server.api`` calls it once while building the app.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.requests import ClientDisconnect

from server.core.env import get_env
from server.core import request_context
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

# ── Global request-body size guard ─────────────────────────────────
# Every JSON endpoint used to accept a body of any size: the only limit in the
# codebase was the 64 MB one inside the connector upload. A single large POST
# could therefore pin memory (the whole body is parsed before a route sees it)
# on an endpoint like /api/memories, /api/ask or /api/import. This is a global
# cap in front of the router; the connector upload keeps its own, larger
# allowance because its base64 payload is a file by design.
#
# The default is generous for JSON yet far below a memory-exhaustion POST.
DEFAULT_MAX_REQUEST_BODY_BYTES = 16 * 1024 * 1024
# Resolved per request, not frozen at import: the token gate in this module
# re-reads its value the same way (issue #132), so a deployment can raise or
# lower the cap through the environment, and the tests can drive a small one.
MAX_REQUEST_BODY_BYTES_ENV = "LEVH_MAX_REQUEST_BODY_BYTES"

# /api/connectors/upload carries a base64 file and enforces its own 64 MB
# decoded cap (connectors.MAX_UPLOAD_BYTES), which is ~85 MB once encoded. It
# is exempt so the global guard cannot reject what the endpoint accepts.
_BODY_LIMIT_EXEMPT_PATHS = {"/api/connectors/upload"}
_BODY_LIMIT_METHODS = {"POST", "PUT", "PATCH"}


def _body_limit_exempt(request: Request) -> bool:
    # Trailing-slash tolerant, like the other path checks here.
    return _canonical_api_path(request.url.path).rstrip("/") in _BODY_LIMIT_EXEMPT_PATHS


def _canonical_api_path(path: str) -> str:
    """Collapse the versioned spelling onto the gate's unversioned vocabulary.

    ``/api/v1/...`` serves the same handlers as ``/api/...`` (issue #193), and
    a boundary that matched only the legacy spelling would silently reopen for
    the versioned alias: the demo's bulk-export refusal, its recall exemption
    and the upload's larger body cap are all keyed by path. Normalizing here
    keeps one decision table for both spellings instead of two that drift.
    """
    if path.startswith("/api/v1/"):
        return "/api/" + path[len("/api/v1/"):]
    if path == "/api/v1":
        return "/api"
    return path


def _body_too_large_response(limit: int) -> JSONResponse:
    return JSONResponse(
        {"detail": f"request body exceeds the {limit // (1024 * 1024)} MB limit"},
        status_code=413,
    )


def max_request_body_bytes() -> int:
    """The live cap; a malformed or non-positive override falls back to default."""
    try:
        value = int(get_env(MAX_REQUEST_BODY_BYTES_ENV, str(DEFAULT_MAX_REQUEST_BODY_BYTES)))
    except ValueError:
        return DEFAULT_MAX_REQUEST_BODY_BYTES
    return value if value > 0 else DEFAULT_MAX_REQUEST_BODY_BYTES


async def _declared_length_guard(request: Request, call_next):
    """Reject a body that declares an over-limit Content-Length before reading.

    Returning a response instead of calling ``call_next`` is how this layer
    refuses without touching the body; Starlette sends whatever is returned.
    """
    if request.method in _BODY_LIMIT_METHODS and not _body_limit_exempt(request):
        raw = request.headers.get("content-length")
        if raw is not None:
            limit = max_request_body_bytes()
            try:
                length = int(raw)
            except ValueError:
                return JSONResponse({"detail": "invalid Content-Length"}, status_code=400)
            if length > limit:
                return _body_too_large_response(limit)
    return await call_next(request)


async def _read_body_within_limit(request: Request, limit: int) -> bytes | None:
    """Read and cache the body, or return None once *limit* is exceeded.

    Content-Length is advisory: a chunked request has none, and a client may
    simply lie, so the declared-length guard alone would leave the guard
    bypassable. Streaming the body and stopping as soon as the running total
    crosses the limit is what makes the cap real — at most one chunk beyond
    the limit is ever held.
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            return None
        chunks.append(chunk)
    body = b"".join(chunks)
    # Cache it: the route's own body read must not re-read the (now consumed)
    # stream and see an empty body.
    request._body = body  # type: ignore[attr-defined]
    return body


async def _body_size_guard(request: Request, call_next):
    """Cap every mutating request body, however its length is declared."""
    if request.method not in _BODY_LIMIT_METHODS or _body_limit_exempt(request):
        return await call_next(request)
    limit = max_request_body_bytes()
    try:
        body = await _read_body_within_limit(request, limit)
    except ClientDisconnect:
        # The client hung up while the body was being read; nothing to answer.
        return JSONResponse({"detail": "client disconnected"}, status_code=400)
    if body is None:
        return _body_too_large_response(limit)
    return await call_next(request)


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


def _plausible_request_id(value: str) -> bool:
    """Whether a caller-supplied ``X-Request-ID`` may be reused.

    Reused verbatim it would be echoed into a response header and every JSON
    log line for the request, so it is held to the alphabet tracing ids
    actually use: a header-injection payload (CR/LF) or an unbounded string is
    replaced with a server-generated id instead. 128 chars is well past every
    real trace id (W3C traceparent is 55).
    """
    return bool(value) and len(value) <= 128 and all(
        char.isalnum() or char in "-_.:" for char in value
    )


#: Probes an orchestrator issues without credentials. ``/api/health`` is
#: liveness and ``/api/readyz`` readiness (issue #145): a container healthcheck
#: cannot attach ``X-LEVH-Token``, so gating them would mean every probe is a
#: 401 and the platform reads a working instance as down. Both are reports on
#: this process, never a path to memory content.
_PROBE_PATHS = {"/api/health", "/api/readyz"}


def _guarded(request: Request) -> bool:
    """Whether this request is subject to the /api gates."""
    path = _canonical_api_path(request.url.path).rstrip("/")
    return path.startswith("/api/") and path not in _PROBE_PATHS


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
    server.api: token first, then the demo boundary. The body-size guard is
    registered first and therefore runs innermost, after the auth and demo
    gates have already passed: a refused request must not first be buffered,
    and an unauthenticated caller must not be able to make the server read a
    large body at all.
    """

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        """Bind a correlation id for everything logged while serving *request*.

        Registered first and therefore outermost: even a gate refusal below is
        logged under the same id, and the header is set on every response
        (including the 401/403/429 ones) so a client can quote it in a bug
        report. A caller-supplied ``X-Request-ID`` is reused when it is
        plausible — it lets a proxy stitch its own tracing id to ours — but the
        value ends up in log lines and a response header, so it is sanitized
        rather than trusted verbatim.
        """
        supplied = (request.headers.get(request_context.REQUEST_ID_HEADER) or "").strip()
        request_id = supplied if _plausible_request_id(supplied) else request_context.new_request_id()
        token = request_context.set_request_id(request_id)
        try:
            response = await call_next(request)
        finally:
            request_context.reset_request_id(token)
        response.headers[request_context.REQUEST_ID_HEADER] = request_id
        return response

    # ``_declared_length_guard`` rejects an over-limit Content-Length before a
    # byte is read; ``_body_size_guard`` streams the body for requests that
    # declare no length (chunked) or lie about it.
    app.middleware("http")(_declared_length_guard)
    app.middleware("http")(_body_size_guard)

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
            path = _canonical_api_path(request.url.path)
            if request.method in ("GET", "HEAD", "OPTIONS"):
                if path in PUBLIC_DEMO_BLOCKED_PATHS:
                    return JSONResponse(
                        {"detail": "forbidden in public demo mode"},
                        status_code=403,
                    )
                return await call_next(request)
            if request.method == "POST" and path in PUBLIC_DEMO_ALLOWED_POSTS:
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
