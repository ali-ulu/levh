"""The generated API docs surface must follow the token gate (issue #144).

``/docs``, ``/redoc`` and ``/openapi.json`` were reachable without a token even
when ``LEVH_TOKEN`` was set, because the gate only covered the ``/api/`` prefix.
A browser cannot attach ``X-LEVH-Token`` to the ``/docs`` document request, so
the surface is withheld entirely while the gate is on — and restored with
``LEVH_ENABLE_API_DOCS=true`` when an operator wants it on a trusted network.

These tests drive the real middleware stack through ``TestClient``; the gate
decides per request, so no module reload is needed.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")
# Refused alongside the rest: leaving it reachable would keep the generated
# surface anonymously confirmable after #144 closed every sibling path.
DOCS_OAUTH_PATH = "/docs/oauth2-redirect"


def _client(monkeypatch: pytest.MonkeyPatch, tmp_path, **env: str) -> TestClient:
    monkeypatch.setenv("EMBEDDER_MODE", "hash")
    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "levh.db"))
    for name in ("LEVH_TOKEN", "LEVH_ENABLE_API_DOCS", "LEVH_PUBLIC_DEMO"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    from server import api

    reloaded = importlib.reload(api)
    reloaded._engine = None
    reloaded._initialized = False
    # TestClient presents itself as a non-loopback peer otherwise, and the
    # remote boundary would reject every request before the gate under test.
    return TestClient(reloaded.app, client=("127.0.0.1", 51234))


@pytest.fixture()
def open_server(monkeypatch, tmp_path):
    """No token: the zero-config local case."""
    client = _client(monkeypatch, tmp_path)
    with client:
        yield client


@pytest.fixture()
def token_server(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path, LEVH_TOKEN="s3cret-token")
    with client:
        yield client


def test_docs_served_when_no_token(open_server):
    """The local, zero-config case keeps the documented /docs URL working."""
    for path in DOCS_PATHS:
        assert open_server.get(path).status_code == 200, path


def test_docs_withheld_when_token_set(token_server):
    """Setting LEVH_TOKEN removes the anonymous route map (issue #144)."""
    for path in (*DOCS_PATHS, DOCS_OAUTH_PATH, "/docs/"):
        response = token_server.get(path)
        assert response.status_code == 404, f"{path} leaked: {response.status_code}"


def test_token_gate_still_protects_api(token_server):
    """Withholding docs must not weaken the /api gate itself."""
    assert token_server.get("/api/stats").status_code == 401
    assert (
        token_server.get("/api/stats", headers={"X-LEVH-Token": "s3cret-token"}).status_code
        == 200
    )


def test_health_stays_open_with_token(token_server):
    """The dashboard polls health before it has a token; that must not regress."""
    assert token_server.get("/api/health").status_code == 200


def test_enable_api_docs_opt_in_restores_surface(monkeypatch, tmp_path):
    """LEVH_ENABLE_API_DOCS=true is the documented way back on a trusted net."""
    client = _client(
        monkeypatch, tmp_path, LEVH_TOKEN="s3cret-token", LEVH_ENABLE_API_DOCS="true"
    )
    with client:
        for path in DOCS_PATHS:
            assert client.get(path).status_code == 200, path
        # The token gate itself is untouched by the opt-in.
        assert client.get("/api/stats").status_code == 401


def test_docs_decision_tracks_the_environment(monkeypatch):
    """The boundary is resolved per call, not frozen at import (issue #132)."""
    from server.routes import deps

    monkeypatch.delenv("LEVH_TOKEN", raising=False)
    monkeypatch.delenv("LEVH_ENABLE_API_DOCS", raising=False)
    assert deps.api_docs_enabled() is True

    monkeypatch.setenv("LEVH_TOKEN", "late-secret")
    assert deps.api_docs_enabled() is False

    monkeypatch.setenv("LEVH_ENABLE_API_DOCS", "true")
    assert deps.api_docs_enabled() is True
