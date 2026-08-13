"""REST surface for the mistake guard."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDER_MODE", "hash")
    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "levh.db"))
    monkeypatch.delenv("LEVH_TOKEN", raising=False)
    from server import api
    from server.core import engine_provider

    # The engine is a module global, so without this reset each test would
    # inherit the previous test's database and see its rows.
    api._engine = None
    api._initialized = False
    engine_provider.set_engine(None)

    # The remote-access boundary is loopback-only without a token, and
    # TestClient otherwise presents itself as a non-local client.
    with TestClient(api.app, client=("127.0.0.1", 51234)) as c:
        yield c

    api._engine = None
    api._initialized = False
    engine_provider.set_engine(None)


def _record(client, **overrides):
    payload = {
        "task": "write README and commit",
        "wrong_action": "used git commit --no-verify",
        "correct_action": "run git commit normally, with the hooks",
        "root_cause": "tried to go faster by skipping the hooks",
        "severity": "high",
    }
    payload.update(overrides)
    return client.post("/api/guard/mistakes", json=payload)


def test_empty_guard_reports_empty_lists(client):
    assert client.get("/api/guard/violations").json() == {"violations": []}
    assert client.get("/api/guard/rules").json() == {"rules": []}


def test_recording_returns_the_rule_it_created(client):
    res = _record(client)
    assert res.status_code == 200, res.text

    body = res.json()
    assert body["pinned"] is True
    assert body["severity"] == "high"
    assert body["statement"].startswith("Do not used git commit --no-verify.")
    assert body["total_violations"] == 1


def test_a_recorded_mistake_appears_in_both_views(client):
    rule_id = _record(client).json()["rule_id"]

    violations = client.get("/api/guard/violations").json()["violations"]
    assert [v["rule_id"] for v in violations] == [rule_id]
    assert violations[0]["severity"] == "high"

    rules = client.get("/api/guard/rules").json()["rules"]
    assert [r["id"] for r in rules] == [rule_id]
    assert rules[0]["correct_action"] == "run git commit normally, with the hooks"
    assert rules[0]["root_cause"] == "tried to go faster by skipping the hooks"


def test_violations_can_be_filtered_by_severity(client):
    _record(client, severity="low", wrong_action="left a TODO in")
    _record(client, severity="critical", wrong_action="dropped the prod table")

    rows = client.get("/api/guard/violations", params={"severity": "critical"}).json()
    assert [v["wrong_action"] for v in rows["violations"]] == ["dropped the prod table"]


def test_rules_can_be_scoped_to_a_project(client):
    _record(client, project="levh")

    assert len(client.get("/api/guard/rules", params={"project": "levh"}).json()["rules"]) == 1
    assert client.get("/api/guard/rules", params={"project": "other"}).json()["rules"] == []


def test_an_incomplete_mistake_is_rejected(client):
    res = _record(client, correct_action="")
    assert res.status_code == 422
    assert "correct_action is required" in res.json()["detail"]


def test_guard_endpoints_require_the_token_when_one_is_set(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDER_MODE", "hash")
    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "levh.db"))
    monkeypatch.setenv("LEVH_TOKEN", "secret")
    import importlib

    from server import api

    reloaded = importlib.reload(api)
    try:
        with TestClient(reloaded.app, client=("127.0.0.1", 51234)) as c:
            assert c.get("/api/guard/violations").status_code == 401
            ok = c.get("/api/guard/violations", headers={"X-LEVH-Token": "secret"})
            assert ok.status_code == 200
    finally:
        monkeypatch.delenv("LEVH_TOKEN", raising=False)
        importlib.reload(api)
