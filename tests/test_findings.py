"""The findings inbox: scrubbing, fingerprint dedupe, and the human decision.

Three properties are load-bearing and each is pinned here:

1. A finding never carries the operator's identity. The inbox is local, but a
   finding is written to be forwarded — pasted into a public issue, mailed,
   screenshotted — and a value that was never written cannot leak later.
2. A repeat updates one row. The reporter is a periodic loop; without the
   fingerprint it would bury the inbox under copies of a single problem.
3. A resolved problem that recurs reopens. Otherwise a regression lands in an
   already-closed row and is never seen again.
"""

from __future__ import annotations

import os
import tempfile

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

os.environ["EMBEDDER_MODE"] = "hash"

from server.core import findings as findings_core  # noqa: E402
from server.core.memory_engine import MemoryEngine  # noqa: E402


@pytest_asyncio.fixture
async def api_client():
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


# ── Scrubbing ────────────────────────────────────────────────────────


def test_scrub_replaces_the_home_directory_with_a_placeholder():
    from pathlib import Path

    text = f"Traceback in {Path.home()}\\levh\\server\\core\\librarian.py"
    assert str(Path.home()) not in findings_core.scrub(text)
    assert "<HOME>" in findings_core.scrub(text)


def test_scrub_removes_the_username_from_any_windows_user_path():
    text = r"could not open C:\Users\someoperator\AppData\Local\stackmemory.db"
    scrubbed = findings_core.scrub(text)
    assert "someoperator" not in scrubbed
    assert "stackmemory.db" in scrubbed  # the useful part survives


def test_scrub_removes_the_username_from_a_posix_home_path():
    scrubbed = findings_core.scrub("failed reading /home/someoperator/.codex/config.toml")
    assert "someoperator" not in scrubbed
    assert "config.toml" in scrubbed


def test_scrub_redacts_secrets_in_evidence():
    scrubbed = findings_core.scrub("startup env had OPENAI_API_KEY=sk-verysecretvalue123456")
    assert "sk-verysecretvalue123456" not in scrubbed


def test_stored_finding_never_contains_the_operator_username():
    """The end-to-end guarantee, stated the way it will actually be broken:
    someone adds a new evidence field and forgets to scrub it."""
    username = os.getenv("USERNAME") or os.getenv("USER") or ""
    if len(username) < 3:
        pytest.skip("no username in this environment to leak")
    row = findings_core.build_row(
        title=f"{username} could not write",
        detail=rf"C:\Users\{username}\levh crashed",
        category="bug",
    )
    assert username.lower() not in row["title"].lower()
    assert username.lower() not in row["detail"].lower()


# ── Fingerprinting ───────────────────────────────────────────────────


def test_same_problem_with_different_timestamps_shares_a_fingerprint():
    a = findings_core.fingerprint("bug", "librarian scan failed", "at 2026-09-05T10:00:00Z line 42")
    b = findings_core.fingerprint("bug", "librarian scan failed", "at 2026-09-05T11:30:00Z line 51")
    assert a == b


def test_different_problems_get_different_fingerprints():
    a = findings_core.fingerprint("bug", "librarian scan failed", "sqlite is locked")
    b = findings_core.fingerprint("bug", "summarizer failed", "connection refused")
    assert a != b


def test_build_row_coerces_an_unknown_category_instead_of_dropping_it():
    row = findings_core.build_row("t", "d", category="nonsense", severity="spicy")
    assert row["category"] == "other"
    assert row["severity"] == "medium"


# ── Dedupe and the human decision ────────────────────────────────────


@pytest.mark.asyncio
async def test_reporting_the_same_finding_twice_keeps_one_row(api_client):
    body = {"title": "librarian scan failed", "detail": "sqlite is locked",
            "category": "bug", "severity": "high", "source": "librarian"}

    first = (await api_client.post("/api/findings", json=body)).json()
    assert first["repeat"] is False
    assert first["occurrences"] == 1

    second = (await api_client.post("/api/findings", json=body)).json()
    assert second["repeat"] is True
    assert second["occurrences"] == 2
    assert second["id"] == first["id"]

    listed = (await api_client.get("/api/findings")).json()["findings"]
    assert len(listed) == 1


@pytest.mark.asyncio
async def test_a_resolved_finding_reopens_when_the_problem_recurs(api_client):
    body = {"title": "startup crash", "detail": "no running event loop", "category": "bug"}
    finding_id = (await api_client.post("/api/findings", json=body)).json()["id"]

    decided = await api_client.post(
        f"/api/findings/{finding_id}/decide",
        json={"status": "resolved", "note": "fixed in PR"},
    )
    assert decided.json()["status"] == "resolved"

    again = (await api_client.post("/api/findings", json=body)).json()
    assert again["reopened"] is True
    assert again["status"] == "open"
    assert again["note"] == "fixed in PR"  # the human's note is not lost


@pytest.mark.asyncio
async def test_decide_rejects_an_unknown_status(api_client):
    finding_id = (await api_client.post(
        "/api/findings", json={"title": "x", "detail": "y"}
    )).json()["id"]
    resp = await api_client.post(
        f"/api/findings/{finding_id}/decide", json={"status": "maybe"}
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_decide_on_a_missing_finding_is_404(api_client):
    resp = await api_client.post(
        "/api/findings/deadbeef1234/decide", json={"status": "ack"}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_listing_filters_by_status_and_reports_counts(api_client):
    a = (await api_client.post(
        "/api/findings", json={"title": "one", "detail": "aaa"}
    )).json()["id"]
    await api_client.post("/api/findings", json={"title": "two", "detail": "bbb"})
    await api_client.post(f"/api/findings/{a}/decide", json={"status": "ignored"})

    open_only = (await api_client.get("/api/findings?status=open")).json()
    assert [f["title"] for f in open_only["findings"]] == ["two"]
    assert open_only["counts"] == {"open": 1, "ignored": 1}

    everything = (await api_client.get("/api/findings?status=")).json()["findings"]
    assert len(everything) == 2


@pytest.mark.asyncio
async def test_reporting_requires_a_title(api_client):
    resp = await api_client.post("/api/findings", json={"title": "   ", "detail": "d"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_delete_removes_the_finding(api_client):
    finding_id = (await api_client.post(
        "/api/findings", json={"title": "x", "detail": "y"}
    )).json()["id"]
    assert (await api_client.delete(f"/api/findings/{finding_id}")).status_code == 200
    assert (await api_client.delete(f"/api/findings/{finding_id}")).status_code == 404


# ── The librarian as a reporter ──────────────────────────────────────


def test_a_disconnected_agent_becomes_one_config_finding():
    from server.core import librarian

    report = {
        "agents": [
            {"agent": "codex", "levh_connected": False,
             "configs": [{"config": "/home/someone/.codex/config.toml",
                          "levh_connected": False}]},
            {"agent": "cline", "levh_connected": True, "configs": []},
        ],
        "activity": {"held_memories": 0},
    }
    rows = librarian.findings_from_report(report)
    assert [r["title"] for r in rows] == ["codex: levh MCP baglantisi yok"]
    assert rows[0]["category"] == "config"
    assert "someone" not in rows[0]["detail"]  # scrubbed on the way in


def test_a_healthy_report_produces_no_findings():
    """An empty inbox must mean "nothing was reported", so a clean scan may
    not manufacture a row saying everything is fine."""
    from server.core import librarian

    rows = librarian.findings_from_report(
        {"agents": [{"agent": "cline", "levh_connected": True, "configs": []}],
         "activity": {"held_memories": 0, "silent_agents": []}}
    )
    assert rows == []


def test_the_same_scan_twice_yields_the_same_fingerprints():
    """The loop repeats every 10 minutes; identical scans must collapse."""
    from server.core import librarian

    report = {
        "agents": [{"agent": "codex", "levh_connected": False,
                    "configs": [{"config": "x", "levh_connected": False}]}],
        "activity": {"held_memories": 0},
    }
    first = [r["id"] for r in librarian.findings_from_report(report)]
    second = [r["id"] for r in librarian.findings_from_report(report)]
    assert first == second


def test_an_unreadable_database_is_a_high_severity_bug():
    from server.core import librarian

    rows = librarian.findings_from_report(
        {"agents": [], "activity": {"error": "database is locked"}}
    )
    assert rows[0]["category"] == "bug"
    assert rows[0]["severity"] == "high"


def test_a_held_queue_below_the_threshold_is_not_a_finding():
    from server.core import librarian

    below = librarian.findings_from_report(
        {"agents": [], "activity": {"held_memories": librarian.HELD_QUEUE_THRESHOLD - 1}}
    )
    at = librarian.findings_from_report(
        {"agents": [], "activity": {"held_memories": librarian.HELD_QUEUE_THRESHOLD}}
    )
    assert below == []
    assert [r["category"] for r in at] == ["memory"]


def test_an_agent_whose_config_location_is_unknown_is_not_a_finding():
    """hermes and aider have no known MCP config path, so "not connected" is
    something we cannot determine — only something we could not check. Filing
    it produces a row the user can never close, and one uncloseable row makes
    the whole inbox unreadable."""
    from server.core import librarian

    rows = librarian.findings_from_report({
        "agents": [
            {"agent": "hermes", "levh_connected": False, "configs": []},
            {"agent": "codex", "levh_connected": False,
             "configs": [{"config": "c", "levh_connected": False}]},
        ],
        "activity": {"held_memories": 0},
    })
    assert [r["title"] for r in rows] == ["codex: levh MCP baglantisi yok"]
