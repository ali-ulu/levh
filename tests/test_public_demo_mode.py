"""Public demo mode — the boundary that keeps a shared demo read-only.

LEVH_PUBLIC_DEMO=true is what stands between an anonymous visitor and the
demo's database. It was verified by hand when it was written; these tests
lock it so a later change cannot quietly reopen the hole.
"""

from __future__ import annotations

import asyncio
import importlib

import pytest
from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path, demo: bool):
    monkeypatch.setenv("EMBEDDER_MODE", "hash")
    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "levh.db"))
    monkeypatch.delenv("LEVH_TOKEN", raising=False)
    if demo:
        monkeypatch.setenv("LEVH_PUBLIC_DEMO", "true")
    else:
        monkeypatch.delenv("LEVH_PUBLIC_DEMO", raising=False)

    from server import api
    from server.core import engine_provider

    # The flag is read at import time, so the module has to be reloaded for
    # the setting to take effect. The engine is a module global too.
    reloaded = importlib.reload(api)
    reloaded._engine = None
    reloaded._initialized = False
    engine_provider.set_engine(None)
    # The remote-access boundary is loopback-only without a token, and
    # TestClient otherwise presents itself as a non-local client.
    return reloaded, TestClient(reloaded.app, client=("127.0.0.1", 51234))


@pytest.fixture()
def demo(monkeypatch, tmp_path):
    module, client = _client(monkeypatch, tmp_path, demo=True)
    with client as c:
        yield c
    monkeypatch.delenv("LEVH_PUBLIC_DEMO", raising=False)
    importlib.reload(module)


@pytest.fixture()
def local(monkeypatch, tmp_path):
    module, client = _client(monkeypatch, tmp_path, demo=False)
    with client as c:
        yield c
    importlib.reload(module)


# ── What a visitor may still do ──────────────────────────────────────


def test_reading_memories_still_works(demo):
    assert demo.get("/api/memories").status_code == 200


def test_health_stays_reachable(demo):
    assert demo.get("/api/health").status_code == 200


# ── What a visitor may not do ────────────────────────────────────────


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/api/memories", {"content": "written by a visitor"}),
        ("put", "/api/memories/abc", {"content": "edited"}),
        ("patch", "/api/memories/abc/pin", {"pinned": True}),
        ("delete", "/api/memories/abc", None),
        ("post", "/api/restore", {"content_b64": "AA=="}),
        ("post", "/api/connectors/upload", {"filename": "x.ics", "content_b64": "AA=="}),
        ("post", "/api/guard/mistakes", {"wrong_action": "a", "correct_action": "b"}),
    ],
)
def test_every_mutating_route_is_refused(demo, method, path, body):
    res = getattr(demo, method)(path, json=body) if body else getattr(demo, method)(path)
    assert res.status_code == 403, f"{method.upper()} {path} returned {res.status_code}"
    assert "public demo mode" in res.json()["detail"]


@pytest.mark.parametrize(
    "path",
    ["/api/export/full.json", "/api/export/full.sqlite", "/api/export/full.pdf"],
)
def test_bulk_exports_are_refused_even_though_they_are_reads(demo, path):
    """A full export hands over the whole database in one request."""
    res = demo.get(path)
    assert res.status_code == 403
    assert "public demo mode" in res.json()["detail"]


def test_search_works_because_it_is_a_read(demo):
    """Recall has to POST to carry its query, but it is a read — and the
    WebSocket path allows the same action, so blocking it over HTTP would
    leave the demo without search."""
    res = demo.post("/api/memories/recall", json={"query": "anything"})
    assert res.status_code == 200
    assert "memories" in res.json()


def test_searching_the_demo_does_not_reshape_it(monkeypatch, tmp_path):
    """Reinforcement resets decay clocks and raises frequency. On a shared
    store an anonymous search must not change what everyone else sees."""
    from server.core.memory_engine import MemoryEngine

    db_path = str(tmp_path / "seeded.db")

    async def seed():
        engine = MemoryEngine(db_path=db_path, embedder_mode="hash")
        await engine.initialize()
        await engine.store("the demo has something to find", memory_type="episodic")
        await engine.shutdown()

    asyncio.run(seed())

    module, client = _client(monkeypatch, tmp_path, demo=True)
    monkeypatch.setenv("SQLITE_DB_PATH", db_path)
    module._engine = MemoryEngine(db_path=db_path, embedder_mode="hash")
    try:
        with client as c:
            before = c.get("/api/memories").json()
            assert before, "the test is meaningless without a memory to reinforce"

            c.post(
                "/api/memories/recall",
                json={"query": "the demo has something to find", "reinforce": True},
            )

            after = c.get("/api/memories").json()
            assert [m["recall_count"] for m in after] == [m["recall_count"] for m in before]
    finally:
        monkeypatch.delenv("LEVH_PUBLIC_DEMO", raising=False)
        importlib.reload(module)


def test_a_visitor_cannot_write_through_the_websocket(demo):
    with demo.websocket_connect("/ws/memory") as ws:
        ws.send_json({"action": "store", "params": {"content": "written over ws"}})
        message = ws.receive_json()

    assert message["type"] == "error"
    assert "forbidden in public demo mode" in message["message"]


def test_reading_over_the_websocket_still_works(demo):
    with demo.websocket_connect("/ws/memory") as ws:
        ws.send_json({"action": "ping"})
        assert ws.receive_json()["type"] == "pong"

        ws.send_json({"action": "stats"})
        assert ws.receive_json()["type"] == "stats"


def test_nothing_a_visitor_sent_reached_the_database(demo):
    demo.post("/api/memories", json={"content": "written by a visitor"})

    body = demo.get("/api/memories").json()
    assert all("written by a visitor" not in m["content"] for m in body)


# ── And that ordinary local use is untouched ─────────────────────────


def test_local_self_hosting_can_still_write(local):
    created = local.post("/api/memories", json={"content": "written locally"})
    assert created.status_code == 200

    body = local.get("/api/memories").json()
    assert any("written locally" in m["content"] for m in body)


def test_local_self_hosting_can_still_write_over_the_websocket(local):
    with local.websocket_connect("/ws/memory") as ws:
        ws.send_json({"action": "store", "params": {"content": "stored over ws"}})
        message = ws.receive_json()

    assert message["type"] != "error"
