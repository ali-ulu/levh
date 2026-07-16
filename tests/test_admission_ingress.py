from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

import pytest
import pytest_asyncio

from server.core.memory_engine import MemoryEngine

SECRET = "password=hunter2 for the production database"
NORMAL = "Atlas production database uses PostgreSQL with daily backups"


@pytest_asyncio.fixture
async def api_client():
    from httpx import ASGITransport, AsyncClient
    import server.api as api_mod

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    if api_mod._engine is not None:
        await api_mod._engine.shutdown()
    api_mod._engine = MemoryEngine(db_path=path, embedder_mode="hash", short_term_max=20)
    await api_mod._engine.initialize()
    api_mod._initialized = True
    transport = ASGITransport(app=api_mod.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await api_mod._engine.shutdown()
    api_mod._engine = None
    api_mod._initialized = False
    if os.path.exists(path):
        os.unlink(path)


@pytest.mark.asyncio
async def test_default_rest_store_redacts_secret_before_persistence(api_client):
    response = await api_client.post(
        "/api/memories",
        json={"content": SECRET, "memory_type": "episodic"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "hunter2" not in body["content"]
    assert "[REDACTED]" in body["content"]
    admission = body["metadata"]["admission"]
    assert admission["action"] == "redact"
    assert admission["reason_codes"] == ["secrets_redacted"]

    audit = (await api_client.get("/api/memories/audit-secrets")).json()["audit"]
    assert audit["flagged"] == 0


@pytest.mark.asyncio
async def test_default_rest_store_blocks_duplicate_and_force_is_audited(api_client):
    first = await api_client.post("/api/memories", json={"content": NORMAL})
    assert first.status_code == 200
    blocked = await api_client.post("/api/memories", json={"content": NORMAL})
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["decision"]["action"] == "reject"

    forced = await api_client.post(
        "/api/memories",
        json={"content": NORMAL, "force": True},
    )
    assert forced.status_code == 200
    assert forced.json()["metadata"]["admission"]["forced"] is True


@pytest.mark.asyncio
async def test_default_mcp_store_tool_uses_admission_gate():
    from mcp.server.fastmcp import FastMCP
    from server.tools.store import register

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = MemoryEngine(db_path=path, embedder_mode="hash")
    await engine.initialize()
    try:
        mcp = FastMCP("test")
        register(mcp, engine)
        result = await mcp.call_tool("store_memory", {"content": SECRET, "memory_type": "episodic"})
        text = str(result)
        assert "redact" in text.lower()
        memories = await engine.list_memories(limit=10)
        assert len(memories) == 1
        assert "hunter2" not in memories[0].content
    finally:
        await engine.shutdown()
        if os.path.exists(path):
            os.unlink(path)


def test_cli_capture_uses_saved_config_and_redacts(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env.pop("SQLITE_DB_PATH", None)
    env.pop("EMBEDDER_MODE", None)
    env["PYTHONPATH"] = str(repo)
    custom = tmp_path / "data" / "capture.db"

    init = subprocess.run(
        [sys.executable, "-m", "server.cli", "init", "--db-path", str(custom), "--embedder-mode", "hash"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert init.returncode == 0, init.stderr
    capture = subprocess.run(
        [sys.executable, "-m", "server.cli", "capture", SECRET],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert capture.returncode == 0, capture.stderr
    assert "redacted" in capture.stdout.lower()
    with sqlite3.connect(custom) as conn:
        content, metadata = conn.execute("SELECT content, metadata FROM memories").fetchone()
    assert "hunter2" not in content
    assert json.loads(metadata)["admission"]["action"] == "redact"


@pytest.mark.asyncio
async def test_secret_audit_preview_never_echoes_secret(tmp_path):
    engine = MemoryEngine(db_path=str(tmp_path / "audit.db"), embedder_mode="hash")
    await engine.initialize()
    try:
        await engine.store(SECRET, memory_type="episodic")  # explicit legacy/raw fixture
        report = await engine.audit_secrets()
        assert report["flagged"] == 1
        preview = report["items"][0]["preview"]
        assert "hunter2" not in preview
        assert "[REDACTED]" in preview
    finally:
        await engine.shutdown()


def test_non_loopback_serve_requires_token(monkeypatch):
    import server.cli as cli
    import uvicorn

    monkeypatch.delenv("LEVH_TOKEN", raising=False)
    called = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: called.append((a, kw)))
    args = argparse.Namespace(host="0.0.0.0", port=8000, reload=False)
    assert cli.cmd_serve(args) == 1
    assert called == []

    monkeypatch.setenv("LEVH_TOKEN", "test-token")
    assert cli.cmd_serve(args) == 0
    assert len(called) == 1


def test_container_defaults_are_local_and_non_root():
    root = Path(__file__).resolve().parents[1]
    compose = (root / "docker-compose.yml").read_text()
    dockerfile = (root / "Dockerfile").read_text()
    assert '"127.0.0.1:8000:8000"' in compose
    assert "USER stackmemory" in dockerfile
    assert "HEALTHCHECK" in dockerfile

@pytest.mark.asyncio
async def test_rest_json_import_is_admission_gated(api_client):
    response = await api_client.post(
        "/api/memories/import",
        json={
            "data": [
                {
                    "content": SECRET,
                    "memory_type": "episodic",
                    "source": "external-json",
                    "embedding": [999.0, 999.0],
                },
                {
                    "content": SECRET,
                    "memory_type": "episodic",
                    "source": "external-json",
                },
            ]
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["gated"] is True
    assert result["imported"] == 1
    assert result["redacted"] == 1
    assert result["duplicates"] == 1

    listed = (await api_client.get("/api/memories")).json()
    assert len(listed) == 1
    assert "hunter2" not in listed[0]["content"]
    assert listed[0]["metadata"]["admission"]["action"] == "redact"
    assert listed[0]["metadata"]["imported_via"] == "json"
    # The untrusted two-dimensional vector was not preserved.
    assert len(listed[0].get("embedding") or []) != 2


@pytest.mark.asyncio
async def test_mcp_json_import_is_admission_gated(tmp_path):
    from mcp.server.fastmcp import FastMCP
    from server.tools.export_import import register

    engine = MemoryEngine(db_path=str(tmp_path / "mcp-import.db"), embedder_mode="hash")
    await engine.initialize()
    try:
        mcp = FastMCP("test")
        register(mcp, engine)
        payload = json.dumps([{"content": SECRET, "memory_type": "episodic"}])
        result = await mcp.call_tool("import_memories", {"data": payload})
        text = str(result).lower()
        assert "admission gate" in text
        assert "redacted=1" in text
        memories = await engine.list_memories(limit=10)
        assert len(memories) == 1
        assert "hunter2" not in memories[0].content
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_low_level_import_does_not_leave_ghost_cache_on_db_failure(tmp_path, monkeypatch):
    engine = MemoryEngine(db_path=str(tmp_path / "ghost.db"), embedder_mode="hash")
    await engine.initialize()
    try:
        item = {
            "content": "Ghost cache test record",
            "memory_type": "episodic",
            "embedding": [0.1] * 384,
        }

        async def fail_store(_memory):
            raise RuntimeError("simulated database failure")

        monkeypatch.setattr(engine.episodic, "store", fail_store)
        assert await engine.import_memories([item]) == 0
        assert engine.vector_store.size == 0
        assert len(engine.short_term) == 0
    finally:
        await engine.shutdown()
