"""The versioned API surface and the frozen OpenAPI contract (issue #193).

Two promises are checked here. First, ``/api/v1`` serves exactly the surface
the unversioned ``/api`` paths do, and the security gates treat both spellings
the same — a version that is only cosmetic would be worse than none, because
the boundary would silently reopen. Second, the committed ``openapi.json`` is
the schema the app generates right now: a change that alters it without the
file being regenerated fails the build instead of reaching clients unnoticed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.api import app

CONTRACT = Path(__file__).resolve().parent.parent / "openapi.json"
#: Unversioned by design: the liveness probe must answer before the versioned
#: surface is known to be usable.
UNVERSIONED = {"/api/health"}
#: Not REST routes: the MCP app is mounted and the websocket has no schema.
NON_REST = {"/api/mcp/sse"}


@pytest.fixture(scope="module")
def schema() -> dict:
    return app.openapi()


def _versioned_paths(schema: dict) -> set[str]:
    return {p for p in schema["paths"] if p.startswith("/api/v1/")}


def _legacy_paths(schema: dict) -> set[str]:
    return {
        p
        for p in schema["paths"]
        if p.startswith("/api/") and not p.startswith("/api/v1/")
    }


def test_the_contract_holds_the_versioned_surface(schema):
    versioned = _versioned_paths(schema)
    assert versioned, "the versioned surface is empty — the contract is not published"


def test_the_unversioned_paths_are_not_in_the_contract(schema):
    """Legacy aliases keep working but must not be published as the schema.

    If they stayed in the schema a client could not tell the versioned
    contract from the compatibility alias, which defeats the freeze.
    """
    assert _legacy_paths(schema) == UNVERSIONED


def test_every_versioned_route_has_its_unversioned_equivalent(schema):
    versioned = _versioned_paths(schema)
    legacy_served = {
        route.path
        for route in app.router.routes
        for route in _iter(route)
        if route.path.startswith("/api/") and not route.path.startswith("/api/v1/")
    }
    expected = {
        "/api/v1" + p[len("/api"):] for p in legacy_served if p not in UNVERSIONED
    }
    assert {_norm(p) for p in versioned} == {_norm(p) for p in expected}, (
        "versioned surface drifted from the routes actually served: "
        f"missing={sorted(expected - versioned)} extra={sorted(versioned - expected)}"
    )


def _norm(path: str) -> str:
    """Path shape with parameter names and converters erased.

    FastAPI publishes ``{entity_id:path}`` in the schema as ``{entity_id}``,
    so comparing raw strings would flag a difference that is not one.
    """
    return re.sub(r"\{[^}]+\}", "{}", path)


def _iter(route):
    from fastapi.routing import APIRoute

    if isinstance(route, APIRoute):
        yield route
    elif hasattr(route, "original_router"):
        for child in route.original_router.routes:
            yield from _iter(child)
    elif hasattr(route, "routes"):
        for child in route.routes:
            yield from _iter(child)


def test_the_frozen_contract_matches_the_code():
    """The point of the freeze: regenerating must be a no-op."""
    frozen = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert frozen == app.openapi(), (
        "openapi.json is stale; regenerate it in the same commit as the change "
        "that altered the schema"
    )


def test_the_schema_is_deterministic():
    """Two generations in one process must be byte-identical.

    FastAPI derives ``operationId`` from route order and handler names; if that
    were unstable the freeze test would fail intermittently instead of
    pointing at a real contract change.
    """
    assert json.dumps(app.openapi(), sort_keys=True) == json.dumps(
        app.openapi(), sort_keys=True
    )


def test_versioned_and_unversioned_paths_agree():
    """A spot check that the alias is wired to the same handler, not a copy.

    The unversioned paths are intentionally absent from the schema, so the
    comparison is against the operations the router actually serves.
    """
    from fastapi.routing import APIRoute

    served = {
        route.path: route
        for route in _iter(app.router)
        if isinstance(route, APIRoute)
    }
    for versioned, legacy in (
        ("/api/v1/stats", "/api/stats"),
        ("/api/v1/memories", "/api/memories"),
    ):
        assert versioned in app.openapi()["paths"]
        clone = served[versioned]
        original = served[legacy]
        assert clone.endpoint is original.endpoint
        assert clone.methods == original.methods
        assert clone.response_model == original.response_model


# ── The gates follow the version ─────────────────────────────────────


def test_the_token_gate_covers_the_versioned_surface(monkeypatch):
    monkeypatch.setenv("LEVH_TOKEN", "s3cret")
    with TestClient(app, client=("127.0.0.1", 51234)) as client:
        assert client.get("/api/v1/stats").status_code == 401
        assert (
            client.get("/api/v1/stats", headers={"X-LEVH-Token": "s3cret"}).status_code
            == 200
        )
        # No version and outside the gate: the liveness probe still answers.
        assert client.get("/api/health").status_code == 200


def test_the_public_demo_gate_covers_the_versioned_surface(monkeypatch):
    monkeypatch.setenv("LEVH_PUBLIC_DEMO", "true")
    with TestClient(app, client=("127.0.0.1", 51234)) as client:
        assert client.get("/api/v1/export/full.json").status_code == 403
        assert client.post("/api/v1/memories", json={}).status_code == 403
        # Recall POSTs to carry its query; it is let through in both spellings.
        assert client.post("/api/v1/memories/recall", json={}).status_code != 403
