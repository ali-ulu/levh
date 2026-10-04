"""Team Memory collaboration tests for #377."""

from __future__ import annotations

import asyncio
import sqlite3

import pytest
import pytest_asyncio

from server.core.agent_tracker import AgentTracker
from server.core import conflict
from server.core.database import CURRENT_SCHEMA_VERSION, Database
from server.core.memory_engine import MemoryEngine
from server.core.tenancy import (
    AuthorizationError,
    Principal,
    bind_principal,
    reset_principal,
)


class _PrincipalContext:
    def __init__(self, *, pid: str, workspace: str, role: str, agent: str | None = None):
        self.value = Principal(id=pid, workspace_id=workspace, role=role, agent=agent)
        self.token = None

    def __enter__(self):
        self.token = bind_principal(self.value)
        return self.value

    def __exit__(self, exc_type, exc, tb):
        assert self.token is not None
        reset_principal(self.token)


@pytest_asyncio.fixture
async def engine(tmp_path):
    eng = MemoryEngine(
        db_path=str(tmp_path / "team-memory.db"),
        embedder_mode="hash",
        short_term_max=50,
    )
    await eng.initialize()
    yield eng
    await eng.shutdown()


def test_decision_conflict_signal_requires_opposition_and_topic_overlap():
    signal = conflict.decision_conflict_signal(
        "database-plan",
        "Use SQLite for the primary database",
        "persistence-choice",
        "Use PostgreSQL for the primary database",
    )
    assert signal is not None
    assert signal[0] == "attribute_value"
    assert "database" in signal[2]


def test_decision_conflict_signal_avoids_unrelated_use_statements():
    assert (
        conflict.decision_conflict_signal(
            "database",
            "Use SQLite for the primary database",
            "cache",
            "Use Redis for the application cache",
        )
        is None
    )


@pytest.mark.asyncio
async def test_handoff_lifecycle_is_workspace_scoped_and_role_gated(engine):
    with _PrincipalContext(pid="backend-1", workspace="team-a", role="editor", agent="codex"):
        handoff = await engine.agent_tracker.create_handoff(
            project="atlas",
            to_agent="cursor",
            title="Finish the settings UI",
            summary="Backend endpoint is ready",
            memory_ids=["mem-a", "mem-b"],
        )
        assert handoff["status"] == "pending"
        assert handoff["to_agent"] == "cursor"
        assert handoff["workspace_id"] == "team-a"

    with _PrincipalContext(pid="reader", workspace="team-a", role="viewer", agent="cursor"):
        visible = await engine.agent_tracker.list_handoffs(project="atlas")
        assert [row["id"] for row in visible] == [handoff["id"]]
        with pytest.raises(AuthorizationError):
            await engine.agent_tracker.create_handoff(
                project="atlas",
                to_agent="codex",
                title="viewer must not write",
            )
        with pytest.raises(AuthorizationError):
            await engine.agent_tracker.accept_handoff(handoff["id"])

    with _PrincipalContext(pid="frontend-1", workspace="team-a", role="editor", agent="cursor"):
        accepted = await engine.agent_tracker.accept_handoff(handoff["id"])
        assert accepted["status"] == "accepted"
        assert accepted["accepted_by"] == "frontend-1"
        completed = await engine.agent_tracker.complete_handoff(handoff["id"])
        assert completed["status"] == "completed"

    with _PrincipalContext(pid="other", workspace="team-b", role="admin", agent="cursor"):
        assert await engine.agent_tracker.list_handoffs(project="atlas") == []


@pytest.mark.asyncio
async def test_handoff_cannot_be_accepted_by_the_wrong_agent(engine):
    with _PrincipalContext(pid="backend", workspace="team-a", role="editor", agent="codex"):
        handoff = await engine.agent_tracker.create_handoff(
            project="atlas",
            to_agent="cursor",
            title="Take the frontend",
        )

    with _PrincipalContext(pid="tester", workspace="team-a", role="editor", agent="vscode"):
        with pytest.raises(AuthorizationError, match="another agent"):
            await engine.agent_tracker.accept_handoff(handoff["id"])


@pytest.mark.asyncio
async def test_shared_decision_conflict_is_signalled_not_auto_resolved(engine):
    with _PrincipalContext(pid="backend", workspace="team-a", role="editor", agent="codex"):
        first = await engine.agent_tracker.create_team_decision(
            project="atlas",
            decision_key="database",
            statement="Use SQLite for the first release",
            rationale="Local-first default",
        )
        assert first["created"] is True
        assert first["contested"] is False
        first_id = first["decision"]["id"]

    with _PrincipalContext(pid="frontend", workspace="team-a", role="editor", agent="cursor"):
        second = await engine.agent_tracker.create_team_decision(
            project="atlas",
            decision_key="database",
            statement="Use PostgreSQL for the first release",
            rationale="Concurrent writers",
        )
        assert second["contested"] is True
        assert {d["status"] for d in second["conflicts"]} == {"contested"}
        second_id = second["decision"]["id"]

    with _PrincipalContext(pid="reader", workspace="team-a", role="viewer", agent="vscode"):
        contested = await engine.agent_tracker.list_team_decisions(
            project="atlas", decision_key="database", status="contested"
        )
        assert {d["id"] for d in contested} == {first_id, second_id}
        with pytest.raises(AuthorizationError):
            await engine.agent_tracker.resolve_team_decision(first_id)

    with _PrincipalContext(pid="owner", workspace="team-a", role="admin"):
        chosen = await engine.agent_tracker.resolve_team_decision(first_id)
        assert chosen["status"] == "active"
        rows = await engine.agent_tracker.list_team_decisions(
            project="atlas", decision_key="database"
        )
        by_id = {row["id"]: row for row in rows}
        assert by_id[first_id]["status"] == "active"
        assert by_id[second_id]["status"] == "superseded"
        assert by_id[second_id]["superseded_by"] == first_id

    with _PrincipalContext(pid="other", workspace="team-b", role="admin"):
        assert await engine.agent_tracker.list_team_decisions(project="atlas") == []


@pytest.mark.asyncio
async def test_semantic_decision_conflict_candidate_review_lifecycle(engine):
    with _PrincipalContext(pid="backend", workspace="team-a", role="editor", agent="codex"):
        first = await engine.agent_tracker.create_team_decision(
            project="atlas",
            decision_key="database-plan",
            statement="Use SQLite for the primary database",
        )
    with _PrincipalContext(pid="architect", workspace="team-a", role="editor", agent="cursor"):
        second = await engine.agent_tracker.create_team_decision(
            project="atlas",
            decision_key="persistence-choice",
            statement="Use PostgreSQL for the primary database",
        )
        detected = await engine.agent_tracker.detect_team_decision_conflicts(
            project="atlas"
        )
        assert detected["new_candidates"] == 1
        assert detected["open_total"] == 1
        summary = await engine.agent_tracker.get_project_collaboration("atlas")
        assert summary["open_decision_conflicts"] == 1
        assert len(summary["decision_conflicts"]) == 1

    with _PrincipalContext(pid="reader", workspace="team-a", role="viewer", agent="vscode"):
        rows = await engine.agent_tracker.list_team_decision_conflicts(project="atlas")
        assert len(rows) == 1
        candidate = rows[0]
        assert {candidate["decision_id_a"], candidate["decision_id_b"]} == {
            first["decision"]["id"],
            second["decision"]["id"],
        }
        assert "database" in candidate["shared_topics"]
        assert candidate["status"] == "open"
        with pytest.raises(AuthorizationError):
            await engine.agent_tracker.review_team_decision_conflict(
                candidate["id"], "confirm"
            )

    with _PrincipalContext(pid="owner", workspace="team-a", role="admin"):
        confirmed = await engine.agent_tracker.review_team_decision_conflict(
            candidate["id"], "confirm"
        )
        assert confirmed["status"] == "confirmed"
        resolved = await engine.agent_tracker.review_team_decision_conflict(
            candidate["id"], "resolve"
        )
        assert resolved["status"] == "resolved"

    with _PrincipalContext(pid="other", workspace="team-b", role="admin"):
        assert await engine.agent_tracker.list_team_decision_conflicts(
            project="atlas", status=None
        ) == []


@pytest.mark.asyncio
async def test_semantic_decision_conflicts_are_idempotent_and_do_not_reopen_dismissed(engine):
    with _PrincipalContext(pid="writer", workspace="team-a", role="editor", agent="codex"):
        await engine.agent_tracker.create_team_decision(
            project="atlas",
            decision_key="database-plan",
            statement="Use SQLite for the primary database",
        )
        await engine.agent_tracker.create_team_decision(
            project="atlas",
            decision_key="persistence-choice",
            statement="Use PostgreSQL for the primary database",
        )
        first = await engine.agent_tracker.detect_team_decision_conflicts(project="atlas")
        second = await engine.agent_tracker.detect_team_decision_conflicts(project="atlas")
        assert first["new_candidates"] == 1
        assert second["new_candidates"] == 0
        candidate = (
            await engine.agent_tracker.list_team_decision_conflicts(project="atlas")
        )[0]

    with _PrincipalContext(pid="owner", workspace="team-a", role="admin"):
        await engine.agent_tracker.review_team_decision_conflict(candidate["id"], "dismiss")

    with _PrincipalContext(pid="writer", workspace="team-a", role="editor", agent="codex"):
        third = await engine.agent_tracker.detect_team_decision_conflicts(project="atlas")
        assert third["new_candidates"] == 0
        assert third["open_total"] == 0


@pytest.mark.asyncio
async def test_semantic_detector_resolves_stale_open_candidate(engine):
    with _PrincipalContext(pid="writer", workspace="team-a", role="editor", agent="codex"):
        first = await engine.agent_tracker.create_team_decision(
            project="atlas",
            decision_key="database-plan",
            statement="Use SQLite for the primary database",
        )
        await engine.agent_tracker.create_team_decision(
            project="atlas",
            decision_key="persistence-choice",
            statement="Use PostgreSQL for the primary database",
        )
        detected = await engine.agent_tracker.detect_team_decision_conflicts(project="atlas")
        assert detected["open_total"] == 1

        # Simulate a later explicit decision lifecycle superseding one side.
        await engine.db.conn.execute(
            "UPDATE team_decisions SET status = 'superseded' WHERE id = ?",
            (first["decision"]["id"],),
        )
        await engine.db.conn.commit()

        rescanned = await engine.agent_tracker.detect_team_decision_conflicts(project="atlas")
        assert rescanned["stale_resolved"] == 1
        assert rescanned["open_total"] == 0
        rows = await engine.agent_tracker.list_team_decision_conflicts(
            project="atlas",
            status="resolved",
        )
        assert len(rows) == 1
        assert rows[0]["reviewed_by"] == "system"


@pytest.mark.asyncio
async def test_semantic_detector_ignores_unrelated_decision_topics(engine):
    with _PrincipalContext(pid="writer", workspace="team-a", role="editor", agent="codex"):
        await engine.agent_tracker.create_team_decision(
            project="atlas",
            decision_key="database",
            statement="Use SQLite for the primary database",
        )
        await engine.agent_tracker.create_team_decision(
            project="atlas",
            decision_key="cache",
            statement="Use Redis for the application cache",
        )
        result = await engine.agent_tracker.detect_team_decision_conflicts(project="atlas")
        assert result["new_candidates"] == 0
        assert result["open_total"] == 0


@pytest.mark.asyncio
async def test_concurrent_decisions_become_contested_without_transaction_bleed(engine):
    with _PrincipalContext(
        pid="writer",
        workspace="team-a",
        role="editor",
        agent="codex",
    ):
        await asyncio.gather(
            engine.agent_tracker.create_team_decision(
                project="atlas",
                decision_key="database",
                statement="Use SQLite",
            ),
            engine.agent_tracker.create_team_decision(
                project="atlas",
                decision_key="database",
                statement="Use PostgreSQL",
            ),
        )
        rows = await engine.agent_tracker.list_team_decisions(
            project="atlas",
            decision_key="database",
        )

    assert len(rows) == 2
    assert {row["status"] for row in rows} == {"contested"}


@pytest.mark.asyncio
async def test_duplicate_shared_decision_is_idempotent(engine):
    with _PrincipalContext(pid="one", workspace="team-a", role="editor", agent="codex"):
        first = await engine.agent_tracker.create_team_decision(
            project="atlas",
            decision_key="api-style",
            statement="Use REST for the public API",
        )
    with _PrincipalContext(pid="two", workspace="team-a", role="editor", agent="cursor"):
        same = await engine.agent_tracker.create_team_decision(
            project="atlas",
            decision_key="api-style",
            statement="  Use REST   for the public API  ",
        )
    assert same["created"] is False
    assert same["decision"]["id"] == first["decision"]["id"]


@pytest.mark.asyncio
async def test_collaboration_summary_includes_handoffs_and_decisions(engine):
    with _PrincipalContext(pid="backend", workspace="team-a", role="editor", agent="codex"):
        await engine.agent_tracker.agent_connect("codex", project="atlas")
        await engine.agent_tracker.create_handoff(
            project="atlas",
            to_agent="cursor",
            title="Wire the dashboard",
        )
        await engine.agent_tracker.create_team_decision(
            project="atlas",
            decision_key="transport",
            statement="Use REST plus SSE",
        )
        summary = await engine.agent_tracker.get_project_collaboration("atlas")

    assert summary["pending_handoffs"] == 1
    assert summary["contested_decisions"] == 0
    assert len(summary["handoffs"]) == 1
    assert len(summary["decisions"]) == 1
    assert {agent["agent_name"] for agent in summary["agents"]} == {"codex"}


@pytest.mark.asyncio
async def test_agent_presence_and_checkpoints_do_not_cross_workspaces(engine):
    with _PrincipalContext(pid="a", workspace="team-a", role="editor", agent="codex"):
        await engine.agent_tracker.agent_connect("codex", project="atlas")
        await engine.agent_tracker.create_checkpoint(
            "codex", "A checkpoint", project="atlas"
        )

    with _PrincipalContext(pid="b", workspace="team-b", role="editor", agent="cursor"):
        await engine.agent_tracker.agent_connect("cursor", project="atlas")
        await engine.agent_tracker.create_checkpoint(
            "cursor", "B checkpoint", project="atlas"
        )
        collab_b = await engine.agent_tracker.get_project_collaboration("atlas")
        assert {a["agent_name"] for a in collab_b["agents"]} == {"cursor"}
        assert {c["agent_name"] for c in collab_b["shared_checkpoints"]} == {"cursor"}

    with _PrincipalContext(pid="a-reader", workspace="team-a", role="viewer", agent="codex"):
        collab_a = await engine.agent_tracker.get_project_collaboration("atlas")
        assert {a["agent_name"] for a in collab_a["agents"]} == {"codex"}
        assert {c["agent_name"] for c in collab_a["shared_checkpoints"]} == {"codex"}


@pytest.mark.asyncio
async def test_agent_tracker_migrates_pre_tenancy_tables(tmp_path):
    path = str(tmp_path / "legacy-agents.db")
    db = Database(path)
    await db.connect()
    try:
        await db.conn.executescript(
            """
            CREATE TABLE agent_sessions (
                id TEXT PRIMARY KEY,
                agent_name TEXT NOT NULL,
                agent_display TEXT NOT NULL,
                session_id TEXT,
                project TEXT,
                status TEXT NOT NULL DEFAULT 'connected',
                connected_at TEXT NOT NULL,
                last_heartbeat_at TEXT NOT NULL,
                disconnected_at TEXT,
                metadata_json TEXT DEFAULT '{}'
            );
            CREATE TABLE agent_checkpoints (
                id TEXT PRIMARY KEY,
                agent_name TEXT NOT NULL,
                session_id TEXT,
                project TEXT,
                checkpoint_type TEXT NOT NULL DEFAULT 'auto',
                title TEXT,
                summary TEXT,
                memory_ids_json TEXT DEFAULT '[]',
                created_at TEXT NOT NULL
            );
            """
        )
        await db.conn.commit()
        tracker = AgentTracker(db, lambda *_: None)
        await tracker.initialize()

        for table in ("agent_sessions", "agent_checkpoints"):
            cursor = await db.conn.execute(f"PRAGMA table_info({table})")
            columns = {row[1] for row in await cursor.fetchall()}
            assert "workspace_id" in columns
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_v7_store_upgrades_to_decision_conflict_schema_v8(tmp_path):
    path = str(tmp_path / "v7.db")
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 7")
    conn.commit()
    conn.close()

    db = Database(path)
    await db.connect()
    try:
        assert db.schema_version == CURRENT_SCHEMA_VERSION == 8
        cursor = await db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('team_handoffs', 'team_decisions', "
            "'team_decision_conflict_candidates') ORDER BY name"
        )
        assert [row[0] for row in await cursor.fetchall()] == [
            "team_decision_conflict_candidates",
            "team_decisions",
            "team_handoffs",
        ]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_team_memory_rest_flow(tmp_path):
    from httpx import ASGITransport, AsyncClient

    import server.api as api_mod

    db_path = str(tmp_path / "team-api.db")
    if api_mod._engine is not None:
        await api_mod._engine.shutdown()
    api_mod._engine = MemoryEngine(
        db_path=db_path,
        embedder_mode="hash",
        short_term_max=50,
    )
    await api_mod._engine.initialize()
    api_mod._initialized = True
    try:
        transport = ASGITransport(app=api_mod.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/api/team/handoffs",
                json={
                    "project": "atlas",
                    "to_agent": "cursor",
                    "title": "Finish the UI",
                    "summary": "Backend is ready",
                    "memory_ids": ["m1"],
                },
            )
            assert r.status_code == 200
            handoff = r.json()
            assert handoff["status"] == "pending"

            r = await client.post(f"/api/team/handoffs/{handoff['id']}/accept")
            assert r.status_code == 200
            assert r.json()["status"] == "accepted"

            first = await client.post(
                "/api/team/decisions",
                json={
                    "project": "atlas",
                    "decision_key": "database",
                    "statement": "Use SQLite",
                    "rationale": "Local-first",
                },
            )
            assert first.status_code == 200
            assert first.json()["contested"] is False

            second = await client.post(
                "/api/team/decisions",
                json={
                    "project": "atlas",
                    "decision_key": "database",
                    "statement": "Use PostgreSQL",
                    "rationale": "Concurrent writers",
                },
            )
            assert second.status_code == 200
            body = second.json()
            assert body["contested"] is True
            assert len(body["conflicts"]) == 2

            r = await client.post(
                f"/api/team/decisions/{first.json()['decision']['id']}/resolve"
            )
            assert r.status_code == 200
            assert r.json()["status"] == "active"

            # Differently-keyed decisions with the same topic can be scanned
            # into a review candidate without changing either decision.
            semantic = await client.post(
                "/api/team/decisions",
                json={
                    "project": "atlas",
                    "decision_key": "storage-plan",
                    "statement": "Use SQLite for the primary database",
                },
            )
            assert semantic.status_code == 200
            semantic2 = await client.post(
                "/api/team/decisions",
                json={
                    "project": "atlas",
                    "decision_key": "persistence-choice",
                    "statement": "Use PostgreSQL for the primary database",
                },
            )
            assert semantic2.status_code == 200
            detect = await client.post(
                "/api/team/decision-conflicts/detect",
                params={"project": "atlas"},
            )
            assert detect.status_code == 200
            assert detect.json()["new_candidates"] >= 1
            listed = await client.get(
                "/api/team/decision-conflicts",
                params={"project": "atlas"},
            )
            assert listed.status_code == 200
            candidate = listed.json()[0]
            reviewed = await client.post(
                f"/api/team/decision-conflicts/{candidate['id']}/review",
                json={"action": "confirm"},
            )
            assert reviewed.status_code == 200
            assert reviewed.json()["conflict"]["status"] == "confirmed"

            invalid = await client.post(
                f"/api/team/decision-conflicts/{candidate['id']}/review",
                json={"action": "bogus"},
            )
            assert invalid.status_code == 422
            missing = await client.post(
                "/api/team/decision-conflicts/missing|candidate/review",
                json={"action": "confirm"},
            )
            assert missing.status_code == 404

            r = await client.get("/api/agents/collaboration/atlas")
            assert r.status_code == 200
            collab = r.json()
            assert collab["pending_handoffs"] == 0
            assert collab["contested_decisions"] == 0
            assert collab["handoffs"][0]["id"] == handoff["id"]
            assert any(d["status"] == "active" for d in collab["decisions"])
    finally:
        await api_mod._engine.shutdown()
        api_mod._engine = None
        api_mod._initialized = False
