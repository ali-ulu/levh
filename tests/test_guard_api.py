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


def test_a_global_rule_is_listed_for_every_project(client):
    """A rule recorded without a project applies everywhere, so scoping the
    list must not hide it from a project-scoped caller."""
    _record(client, project=None)

    levh = client.get("/api/guard/rules", params={"project": "levh"}).json()["rules"]
    other = client.get("/api/guard/rules", params={"project": "other"}).json()["rules"]

    assert len(levh) == 1 and levh[0]["project"] is None
    assert len(other) == 1


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


# ── POST /api/guard/check — the pre-action gate (#337) ────────────────


def _check(client, **overrides):
    payload = {"tool_name": "Bash", "action_text": "git commit --no-verify -m wip"}
    payload.update(overrides)
    return client.post("/api/guard/check", json=payload)


def test_check_warns_on_a_recorded_mistake(client):
    _record(client)

    body = _check(client).json()

    assert body["decision"] == "warn"
    assert body["checked_rules"] == 1
    assert body["tool_name"] == "Bash"
    assert body["matched_rules"][0]["rule_id"]
    assert body["matched_rules"][0]["matched_terms"]


def test_check_allows_an_unrelated_action(client):
    _record(client)

    body = _check(client, action_text="pytest -q tests/").json()

    assert body["decision"] == "allow"
    assert body["matched_rules"] == []


def test_check_never_blocks(client):
    """Issue #337's false-positive policy is encoded here: the gate is
    advisory. A `block` verdict would make LEVH an enforcement layer."""
    _record(client, severity="critical")

    assert _check(client).json()["decision"] != "block"


def test_check_requires_an_action_text(client):
    assert client.post("/api/guard/check", json={"tool_name": "Bash"}).status_code == 422


def test_check_is_read_only(client):
    """Asking must not record anything — no violation, no memory."""
    _record(client)
    before = client.get("/api/guard/violations").json()

    _check(client)

    assert client.get("/api/guard/violations").json() == before

