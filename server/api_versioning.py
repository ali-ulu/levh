"""Versioned API surface: ``/api/v1`` as the frozen contract.

The REST surface grew to ~100 routes under a bare ``/api/`` with no version in
the path, so a schema change shipped straight to every client — the dashboard,
MCP bridges, user scripts — with no way to tell a compatible edit from a
breaking one (issue #193, the lost first half of #152).

This module serves the same handlers under ``/api/v1/...`` and makes the
*versioned* paths the published contract: the legacy unversioned paths keep
working (they are the compatibility alias), but they are hidden from the
OpenAPI schema, which is what the committed ``openapi.json`` freezes and CI
checks for drift.

The aliases are built by cloning each ``APIRoute`` rather than by including the
routers a second time with ``prefix="/api/v1"``. The prefixes of these routers
live inside the route paths (``/api/memories``), not on ``include_router``
call, so a prefix would yield ``/api/v1/api/memories``. Cloning also keeps the
OpenAPI parameters and response models identical by construction, which is the
whole point of a frozen contract.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator

from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute
from starlette.routing import Mount

API_PREFIX = "/api"
#: The version whose paths form the published contract.
API_VERSION = "v1"
VERSIONED_PREFIX = f"{API_PREFIX}/{API_VERSION}"
#: Unversioned and therefore outside the versioned contract by design.
UNVERSIONED_PATHS = frozenset({"/api/health"})


def _iter_api_routes(routes: list) -> Iterator[APIRoute]:
    """Every ``APIRoute`` reachable from *routes*, through include wrappers.

    FastAPI no longer flattens included routers into ``app.routes``; each
    ``include_router`` call leaves an ``_IncludedRouter`` wrapper holding the
    source router. Walking into it is what lets the versioning step see the
    real ``APIRoute`` objects it must clone.
    """
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        elif hasattr(route, "original_router"):
            yield from _iter_api_routes(route.original_router.routes)
        elif hasattr(route, "routes"):
            yield from _iter_api_routes(route.routes)


def versioned_clone(route: APIRoute) -> APIRoute:
    """``route`` re-served under ``/api/v1`` with equivalent metadata."""
    clone = copy.copy(route)
    clone.path = VERSIONED_PREFIX + route.path[len(API_PREFIX):]
    clone.path_format = clone.path
    # Starlette compiles ``path_regex`` in ``Route.__init__`` and matches
    # against the live ``path`` afterwards; ``copy.copy`` carries the compiled
    # matcher over, so without dropping it the clone keeps matching the
    # *unversioned* path and the versioned URL 404s.
    clone.__dict__.pop("path_regex", None)
    # The legacy route is hidden from the schema, so the versioned one is the
    # only ``operationId`` of each pair; an explicit suffix keeps it derived
    # from the handler rather than from a positional accident.
    clone.operation_id = f"{route.operation_id or route.unique_id}_v1"
    return clone


def install_versioned_surface(app: FastAPI) -> None:
    """Serve every ``/api/*`` route under ``/api/v1`` as the contract.

    Pure side effect: it installs the aliases and publishes the versioned
    schema, returning nothing. Building the surface must not mutate the routes
    it reads from: the router modules are module-level
    objects that outlive any one app, and ``importlib.reload(server.api)``
    rebuilds the app without reloading them. Marking the originals hidden would
    therefore leak into the next build, whose clones would inherit the flag and
    publish nothing. The legacy paths are kept out of the schema by filtering
    it instead, which is idempotent by construction.
    """
    api_routes = [
        route
        for route in _iter_api_routes(app.router.routes)
        if route.path.startswith(API_PREFIX + "/") and route.path not in UNVERSIONED_PATHS
    ]

    versioned = APIRouter()
    versioned.routes.extend(versioned_clone(route) for route in api_routes)
    app.include_router(versioned)
    _publish_only_versioned(app)

    # ``include_router`` appends after the dashboard catch-all mount, which
    # would swallow every versioned path (the Mount matches ``/{path}``). Move
    # the wrapper in front of the first mount so the aliases resolve.
    included = app.router.routes.pop()
    insert_at = next(
        (i for i, route in enumerate(app.router.routes) if isinstance(route, Mount)),
        len(app.router.routes),
    )
    app.router.routes.insert(insert_at, included)


def _publish_only_versioned(app: FastAPI) -> None:
    """Drop the unversioned ``/api/*`` paths from the generated schema.

    The aliases stay served — they are the backward-compatibility surface — but
    a client reading the contract sees one path per operation, under ``/api/v1``.
    """
    build_schema = app.openapi

    def openapi() -> dict:
        schema = build_schema()
        paths = schema.get("paths", {})
        for path in [
            p
            for p in paths
            if p.startswith(API_PREFIX + "/")
            and not p.startswith(VERSIONED_PREFIX + "/")
            and p not in UNVERSIONED_PATHS
        ]:
            del paths[path]
        return schema

    app.openapi = openapi