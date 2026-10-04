"""Tests for background sync jobs and the opt-in auto-feed loop (#374).

Offline & deterministic — EMBEDDER_MODE=hash, local_files/git fixtures only,
no network. The job runner is process-global, so every test resets it.
"""

import asyncio
import os
import sys
import tempfile

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["EMBEDDER_MODE"] = "hash"
os.environ.pop("OPENAI_API_KEY", None)

from server.core.auto_sync import (
    load_auto_sync_jobs,
    resolve_env_refs,
    run_jobs_once,
)
from server.core.memory_engine import MemoryEngine
from server.core.sync_jobs import get_runner, reset_runner


@pytest.fixture(autouse=True)
def _fresh_runner():
    reset_runner()
    yield
    reset_runner()


@pytest_asyncio.fixture
async def engine():
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    eng = MemoryEngine(db_path=db_path, embedder_mode="hash", short_term_max=10)
    await eng.initialize()
    yield eng
    await eng.shutdown()
    if os.path.exists(db_path):
        os.unlink(db_path)


def _note_dir() -> str:
    tmpdir = tempfile.mkdtemp()
    with open(os.path.join(tmpdir, "note.md"), "w", encoding="utf-8") as handle:
        handle.write("# Feed probe\n\nThe bridge reopens at midnight.")
    return tmpdir


# ── env: references ────────────────────────────────────────────────


def test_resolve_env_refs_nested_and_passthrough(monkeypatch):
    monkeypatch.setenv("LEVHTEST_AUTO_FEED", "s3cr3t")
    assert resolve_env_refs("env:LEVHTEST_AUTO_FEED") == "s3cr3t"
    assert resolve_env_refs("plain") == "plain"
    assert resolve_env_refs({"a": ["env:LEVHTEST_AUTO_FEED", 1]}) == {
        "a": ["s3cr3t", 1]
    }
    with pytest.raises(KeyError):
        resolve_env_refs("env:LEVHTEST_DEFINITELY_UNSET_VAR")
    with pytest.raises(KeyError):
        resolve_env_refs("env:  ")


# ── config loader ──────────────────────────────────────────────────


def test_auto_sync_defaults_to_disabled():
    enabled, interval, jobs = load_auto_sync_jobs({})
    assert enabled is False
    assert jobs == []
    assert interval > 0


def test_auto_sync_malformed_section_refuses():
    with pytest.raises(ValueError):
        load_auto_sync_jobs({"auto_sync": {"enabled": True, "interval_seconds": -5}})
    with pytest.raises(ValueError):
        load_auto_sync_jobs({"auto_sync": {"enabled": True, "jobs": "nope"}})
    with pytest.raises(ValueError):
        load_auto_sync_jobs({"auto_sync": {"enabled": True, "jobs": [{}]}})


def test_auto_sync_valid_section_parses():
    cfg = {
        "auto_sync": {
            "enabled": True,
            "interval_seconds": 60,
            "jobs": [
                {"connector": "git", "config": {"repo_path": "/x"}, "project": "p"}
            ],
        }
    }
    enabled, interval, jobs = load_auto_sync_jobs(cfg)
    assert enabled is True
    assert interval == 60
    assert jobs[0]["connector"] == "git"
    assert jobs[0]["use_gate"] is True


# ── job runner ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_runner_completes_local_files_job(engine):
    tmpdir = _note_dir()
    runner = get_runner()
    job = await runner.submit(
        engine, "local_files", {"directory": tmpdir}, project="feed-proj"
    )
    assert job["status"] == "pending"
    for _ in range(100):
        seen = runner.get_job(job["job_id"])
        if seen["status"] in ("done", "error"):
            break
        await asyncio.sleep(0.05)
    assert seen["status"] == "done", seen.get("error")
    assert seen["result"]["stored"] >= 1
    assert seen["result"]["timing_ms"]["job_total"] >= 0
    assert seen["finished_at"]


@pytest.mark.asyncio
async def test_runner_unknown_connector_is_error_not_crash(engine):
    runner = get_runner()
    job = await runner.submit(engine, "does_not_exist", {})
    for _ in range(100):
        seen = runner.get_job(job["job_id"])
        if seen["status"] in ("done", "error"):
            break
        await asyncio.sleep(0.05)
    assert seen["status"] == "error"
    assert seen["finished_at"]
    assert runner.get_job("nope") is None
    assert isinstance(runner.list_jobs(), list)


# ── one-shot loop ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_jobs_once_feeds_and_isolates_failures(engine):
    tmpdir = _note_dir()
    reports = await run_jobs_once(
        engine,
        [
            {"connector": "local_files",
             "config": {"directory": tmpdir}, "project": "feed-proj"},
            {"connector": "does_not_exist", "config": {}},
        ],
    )
    assert reports[0]["stored"] >= 1
    assert "error" in reports[1]


@pytest.mark.asyncio
async def test_run_jobs_once_missing_env_fails_loud(engine, monkeypatch):
    monkeypatch.delenv("LEVHTEST_DEFINITELY_UNSET_VAR", raising=False)
    reports = await run_jobs_once(
        engine,
        [{"connector": "local_files",
          "config": {"directory": "env:LEVHTEST_DEFINITELY_UNSET_VAR"}}],
    )
    assert "error" in reports[0]


# ── routes: background submit + poll ───────────────────────────────


@pytest_asyncio.fixture
async def api_client():
    from httpx import ASGITransport, AsyncClient

    import server.api as api_mod

    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    if api_mod._engine is not None:
        await api_mod._engine.shutdown()
    api_mod._engine = MemoryEngine(db_path=db_path, embedder_mode="hash", short_term_max=50)
    await api_mod._engine.initialize()
    api_mod._initialized = True
    transport = ASGITransport(app=api_mod.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await api_mod._engine.shutdown()
    api_mod._engine = None
    api_mod._initialized = False
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.mark.asyncio
async def test_api_background_sync_returns_202_and_job_completes(api_client):
    tmpdir = _note_dir()
    resp = await api_client.post(
        "/api/connectors/sync",
        json={"connector": "local_files", "config": {"directory": tmpdir},
              "background": True},
    )
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]

    for _ in range(100):
        poll = await api_client.get(f"/api/connectors/sync-jobs/{job_id}")
        assert poll.status_code == 200
        if poll.json()["status"] in ("done", "error"):
            break
        await asyncio.sleep(0.05)
    body = poll.json()
    assert body["status"] == "done"
    assert body["result"]["stored"] >= 1

    listed = await api_client.get("/api/connectors/sync-jobs")
    assert any(j["job_id"] == job_id for j in listed.json())

    missing = await api_client.get("/api/connectors/sync-jobs/nope")
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_api_sync_unresolved_env_is_400(api_client):
    resp = await api_client.post(
        "/api/connectors/sync",
        json={"connector": "local_files",
              "config": {"directory": "env:LEVHTEST_DEFINITELY_UNSET_VAR"}},
    )
    assert resp.status_code == 400
