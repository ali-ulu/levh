"""Readiness probe and its token exemption (issue #145).

``/api/health`` is liveness: it answers from the process's own state and stays
up when the store is wedged or the embedder has silently fallen back, which
made the Docker ``HEALTHCHECK`` a lie. ``/api/readyz`` does the round-trip an
orchestrator actually needs, so these tests drive the real middleware stack and
assert both the report and the status code.
"""

from __future__ import annotations

import asyncio
import json
import logging

import pytest
from fastapi.testclient import TestClient

from server.core.env import accepted_env_var_names


@pytest.fixture()
def client(monkeypatch, tmp_path):
    # The canonical spelling is not the only one ``get_env`` reads:
    # a machine-level ``LEVH_EMBEDDER_MODE`` (or ``STACKMEMORY_`` legacy)
    # outranks ``EMBEDDER_MODE`` and would silently defeat the setenv
    # below — reddening these tests locally while CI, which exports only
    # the plain spelling, stays green. Scrub every accepted spelling
    # first, then pin the one this suite means (issue #519 made readyz
    # sensitive to the resolved mode, so the pin must actually stick).
    for spelling in accepted_env_var_names("EMBEDDER_MODE"):
        monkeypatch.delenv(spelling, raising=False)
    monkeypatch.setenv("EMBEDDER_MODE", "hash")
    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "levh.db"))
    monkeypatch.delenv("LEVH_TOKEN", raising=False)
    from server import api
    from server.core import engine_provider

    api._engine = None
    api._initialized = False
    engine_provider.set_engine(None)

    with TestClient(api.app, client=("127.0.0.1", 51234)) as c:
        yield c

    api._engine = None
    api._initialized = False
    engine_provider.set_engine(None)


def test_ready_reports_each_dependency(client):
    response = client.get("/api/readyz")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ready"
    assert body["database"] == "ok"
    assert body["embedder_ready"] is True
    assert body["reasons"] == []


# -- auto-update visibility on the probes (issue #522) ------------------------


def test_health_reports_auto_update_state(client, monkeypatch, tmp_path):
    """A skipped auto-update must be visible where the dashboard looks."""
    monkeypatch.setenv("LEVH_SUPERVISOR_LOG_DIR", str(tmp_path))
    from server.core import update_state

    update_state.write_update_state(
        {"status": "skipped", "reason": "checkout is on 'fix/x', auto-update only follows 'main'"},
        path=tmp_path / "update-state.json",
    )
    body = client.get("/api/health").json()
    assert body["auto_update"]["present"] is True
    assert body["auto_update"]["needs_attention"] is True
    assert "fix/x" in body["auto_update"]["reason"]


def test_health_auto_update_is_absent_and_harmless_without_a_supervisor(client, monkeypatch, tmp_path):
    monkeypatch.setenv("LEVH_SUPERVISOR_LOG_DIR", str(tmp_path))
    body = client.get("/api/health").json()
    assert body["auto_update"]["present"] is False
    assert body["auto_update"]["needs_attention"] is False


def test_readyz_stays_ready_but_names_a_skipped_auto_update(client, monkeypatch, tmp_path):
    """The server serves fine; it is only running older code than upstream."""
    monkeypatch.setenv("LEVH_SUPERVISOR_LOG_DIR", str(tmp_path))
    from server.core import update_state

    update_state.write_update_state(
        {"status": "skipped", "reason": "checkout is on 'fix/x', auto-update only follows 'main'"},
        path=tmp_path / "update-state.json",
    )
    response = client.get("/api/readyz")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ready"
    assert body["auto_update"]["needs_attention"] is True
    assert any("auto-update" in reason for reason in body["reasons"])


def test_ready_is_served_under_the_versioned_contract_too(client):
    assert client.get("/api/v1/readyz").status_code == 200


def test_ready_is_exempt_from_the_token_gate(monkeypatch, tmp_path):
    """A container healthcheck carries no token; it must still get an answer."""
    for spelling in accepted_env_var_names("EMBEDDER_MODE"):
        monkeypatch.delenv(spelling, raising=False)
    monkeypatch.setenv("EMBEDDER_MODE", "hash")
    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "levh.db"))
    monkeypatch.setenv("LEVH_TOKEN", "s3cret-token")
    from server import api
    from server.core import engine_provider

    api._engine = None
    api._initialized = False
    engine_provider.set_engine(None)

    with TestClient(api.app, client=("127.0.0.1", 51234)) as c:
        assert c.get("/api/readyz").status_code == 200
        # The gate itself is untouched: a protected path still fails closed.
        assert c.get("/api/stats").status_code == 401

    api._engine = None
    api._initialized = False
    engine_provider.set_engine(None)


class _Cursor:
    def __init__(self, row):
        self._row = row

    async def fetchone(self):
        return self._row

    async def close(self):
        return None


class _BrokenConn:
    async def execute(self, *_args, **_kwargs):
        raise OSError("database is locked")


class _FakeDb:
    def __init__(self, *, broken: bool):
        self.conn = _BrokenConn() if broken else self
        self.db_path = ":memory:"

    async def execute(self, *_args, **_kwargs):
        return _Cursor((1,))

    async def runtime_status(self):
        return {"schema_version": "1", "schema_current": "1"}


class _FakeEmbedder:
    mode = "hash"
    fallback_reason = None


class _FakeEngine:
    def __init__(self, *, db_broken=False, fallback=None):
        self.db = _FakeDb(broken=db_broken)
        self._embedder = _FakeEmbedder()
        self._embedder.fallback_reason = fallback
        self._embedder_mode = "hash"


def test_not_ready_when_database_is_unreachable():
    from server.routes.system import ready

    response = asyncio.run(ready(engine=_FakeEngine(db_broken=True)))

    assert response.status_code == 503
    body = json.loads(response.body)
    assert body["status"] == "not_ready"
    assert body["database"] == "unavailable"
    assert any("database" in reason for reason in body["reasons"])


def test_not_ready_when_embedder_fell_back():
    from server.routes.system import ready

    response = asyncio.run(ready(engine=_FakeEngine(fallback="openai key rejected")))

    assert response.status_code == 503
    body = json.loads(response.body)
    assert body["embedder_ready"] is False
    assert any("embedder" in reason for reason in body["reasons"])


class _LazyEngine:
    """An engine whose embedder has not been created yet.

    The engine creates it on first store/recall, so every probe that
    arrives before that point sees ``_embedder = None`` (issue #519).
    """

    def __init__(self, *, mode: str):
        self.db = _FakeDb(broken=False)
        self._embedder = None
        self._embedder_mode = mode


def test_readyz_reports_pending_hash_fallback_before_first_use(monkeypatch):
    """A probe before first embed must not bless a would-be hash fallback.

    ``auto`` resolving to hash is a degraded server; readiness used to
    answer ``ready`` for it until the first store/recall actually paid
    for the fallback — the exact false readiness of issue #519.
    """
    from server.core import embedder as embedder_module
    from server.routes.system import ready

    monkeypatch.setattr(embedder_module, "local_embedder_available", lambda: False)

    response = asyncio.run(ready(engine=_LazyEngine(mode="auto")))

    assert response.status_code == 503
    body = json.loads(response.body)
    assert body["embedder_mode"] == "hash"
    assert body["embedder_ready"] is False
    assert any("embedder" in reason for reason in body["reasons"])


def test_readyz_explicit_hash_before_first_use_stays_ready(monkeypatch):
    """Asking for hash is a choice: a hash server is not a degraded server."""
    from server.core import embedder as embedder_module
    from server.routes.system import ready

    monkeypatch.setattr(embedder_module, "local_embedder_available", lambda: False)

    response = asyncio.run(ready(engine=_LazyEngine(mode="hash")))

    # A ready probe returns the payload dict directly; only a degraded one
    # is wrapped in a 503 JSONResponse.
    assert response["status"] == "ready", response
    assert response["embedder_ready"] is True
    assert response["reasons"] == []


def test_config_reports_would_be_fallback_before_first_use(monkeypatch):
    """The Settings page must show the truth before anything embeds, too."""
    from server.core import embedder as embedder_module

    monkeypatch.setattr(embedder_module, "local_embedder_available", lambda: False)

    async def _empty_stats():
        return {}

    class _Continuity:
        async def continuity_stats(self):
            return {}

    class _ConfigEngine(_LazyEngine):
        def __init__(self, mode: str):
            super().__init__(mode=mode)
            self.db.db_path = ":memory:"
            self.db.recall_log_stats = _empty_stats
            self.db.continuity_log = _Continuity()
            self.short_term = type("S", (), {"max_size": 50})()
            self.scorer = type(
                "C",
                (),
                {
                    "w": type("W", (), {"alpha": 1, "beta": 1, "gamma": 1, "delta": 1})(),
                    "half_life_hours": 168.0,
                    "reinforcement_gain": 1.5,
                    "max_stability_hours": 168,
                },
            )()
            self.auto_summarize = True

        def _recall_log_config(self):
            return False, 7

    from server.routes.system import get_config

    body = asyncio.run(get_config(engine=_ConfigEngine(mode="auto")))

    assert body["requested_embedder_mode"] == "auto"
    assert body["embedder_mode"] == "hash"
    assert body["embedder_fallback_reason"]


# ── Deferred local-embedder load, surfaced on the probes (issue #525) ─


def test_readyz_names_a_warming_embedder_without_failing():
    """A server still loading its model serves fine; it must not read as 503.

    The whole point of #525 is that a cold local embedder used to stall the
    first request for ~2.5 minutes while every probe said "ready". The fix
    reports ``warming`` as a *reason* — visible, but not a failure an
    orchestrator would restart the server over.
    """
    from server.routes.system import ready

    class _WarmingEmbedder:
        mode = "local"
        fallback_reason = None
        warm_state = "warming"

    class _WarmingEngine(_LazyEngine):
        def __init__(self):
            super().__init__(mode="local")
            self._embedder = _WarmingEmbedder()

    response = asyncio.run(ready(engine=_WarmingEngine()))

    assert response["status"] == "ready", response
    assert response["embedder_ready"] is True
    assert response["embedder_state"] == "warming"
    assert any("warming" in reason for reason in response["reasons"])


def test_readyz_reports_a_cold_embedder_before_the_load_starts():
    """A cold model (available, not loading yet) is named too."""
    from server.routes.system import ready

    class _ColdEmbedder:
        mode = "local"
        fallback_reason = None
        warm_state = "cold"

    class _ColdEngine(_LazyEngine):
        def __init__(self):
            super().__init__(mode="local")
            self._embedder = _ColdEmbedder()

    response = asyncio.run(ready(engine=_ColdEngine()))

    assert response["status"] == "ready", response
    assert response["embedder_state"] == "cold"
    assert any("cold" in reason for reason in response["reasons"])


def test_readyz_has_no_embedder_reason_once_ready():
    """A loaded embedder adds nothing to ``reasons`` — no false alarm."""
    from server.routes.system import ready

    class _ReadyEmbedder:
        mode = "local"
        fallback_reason = None
        warm_state = "ready"

    class _ReadyEngine(_LazyEngine):
        def __init__(self):
            super().__init__(mode="local")
            self._embedder = _ReadyEmbedder()

    response = asyncio.run(ready(engine=_ReadyEngine()))

    assert response["embedder_state"] == "ready"
    assert not any("embedder" in reason for reason in response["reasons"])


def test_health_reports_embedder_state_from_the_published_engine(client):
    """Liveness names the load lifecycle so a warming server is not "hung"."""
    body = client.get("/api/health").json()

    assert body["embedder_state"] == "ready"


def test_health_embedder_state_is_ready_without_an_engine(monkeypatch, tmp_path):
    """Liveness must answer before any engine exists, and not create one."""
    from server import api
    from server.core import engine_provider

    monkeypatch.setenv("EMBEDDER_MODE", "hash")
    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "levh.db"))
    api._engine = None
    api._initialized = False
    engine_provider.set_engine(None)

    from server.routes.system import _liveness_report

    assert _liveness_report()["embedder_state"] == "ready"
    assert api._engine is None, "liveness must not build an engine"


def test_health_alias_serves_the_same_liveness_report(client):
    """``/health`` must answer, not 404 — a poller that always sees the
    server down is worse than no poller (issue #520)."""
    alias = client.get("/health")
    canonical = client.get("/api/health")

    assert alias.status_code == 200, alias.text
    assert alias.json() == canonical.json()


def test_health_alias_is_exempt_from_the_token_gate(monkeypatch, tmp_path):
    """A monitor polling the conventional path carries no token."""
    monkeypatch.setenv("EMBEDDER_MODE", "hash")
    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "levh.db"))
    monkeypatch.setenv("LEVH_TOKEN", "s3cret-token")
    from server import api
    from server.core import engine_provider

    api._engine = None
    api._initialized = False
    engine_provider.set_engine(None)

    with TestClient(api.app, client=("127.0.0.1", 51234)) as c:
        assert c.get("/health").status_code == 200
        # The gate itself is untouched: a protected path still fails closed.
        assert c.get("/api/stats").status_code == 401

    api._engine = None
    api._initialized = False
    engine_provider.set_engine(None)


def test_health_alias_is_not_published_in_the_contract(client):
    """``/health`` is a compatibility spelling, not a second contract
    surface — ``/api/health`` stays the one published liveness path."""
    schema = client.get("/openapi.json").json()

    assert "/api/health" in schema["paths"]
    assert "/health" not in schema["paths"]


def test_request_id_is_generated_and_echoed(client):
    response = client.get("/api/health")

    assert response.headers["X-Request-ID"]


def test_plausible_caller_request_id_is_reused(client):
    response = client.get("/api/health", headers={"X-Request-ID": "trace-abc.123"})

    assert response.headers["X-Request-ID"] == "trace-abc.123"


def test_implausible_caller_request_id_is_replaced(client):
    response = client.get("/api/health", headers={"X-Request-ID": "bad id\ninjected"})

    assert response.headers["X-Request-ID"] != "bad id\ninjected"
    assert response.headers["X-Request-ID"]


def test_structured_log_record_is_json(monkeypatch, capsys):
    from server.core import logging as levh_logging

    monkeypatch.setenv(levh_logging.LOG_JSON_ENV, "1")
    levh_logging.emit(logging.getLogger("test"), "store_ok", memory_id="m1")

    line = capsys.readouterr().out.strip()
    payload = json.loads(line)
    assert payload["event"] == "store_ok"
    assert payload["memory_id"] == "m1"
    assert payload["level"] == "INFO"


def _run_serve_banner(monkeypatch, capsys, tmp_path, *, json_mode):
    """Drive cmd_serve up to the banner, stubbing out uvicorn."""
    import argparse

    import uvicorn

    from server.commands import diagnostics

    monkeypatch.chdir(tmp_path)
    if json_mode:
        monkeypatch.setenv("LEVH_LOG_JSON", "1")
    else:
        monkeypatch.delenv("LEVH_LOG_JSON", raising=False)
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)

    args = argparse.Namespace(host="127.0.0.1", port=9123, reload=False)
    assert diagnostics.cmd_serve(args) == 0
    return capsys.readouterr().out


def test_serve_banner_is_json_when_log_json_enabled(monkeypatch, capsys, tmp_path):
    out = _run_serve_banner(monkeypatch, capsys, tmp_path, json_mode=True)

    lines = [line for line in out.strip().splitlines() if line.strip()]
    payloads = [json.loads(line) for line in lines]
    events = {payload["event"] for payload in payloads}
    assert "serve_starting" in events
    starting = next(p for p in payloads if p["event"] == "serve_starting")
    assert starting["host"] == "127.0.0.1"
    assert starting["port"] == 9123


def test_serve_banner_stays_plaintext_when_log_json_unset(monkeypatch, capsys, tmp_path):
    out = _run_serve_banner(monkeypatch, capsys, tmp_path, json_mode=False)

    assert "Starting LEVH API on 127.0.0.1:9123" in out
    assert "Dashboard: http://127.0.0.1:9123/" in out
    for line in out.strip().splitlines():
        assert not line.lstrip().startswith("{"), line