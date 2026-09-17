"""Regression: online status must be scoped to the agent being asked about (#125).

``AgentUsageService._is_agent_session`` was a placeholder that ignored its
arguments and returned ``True``, so every presence entry counted for every
agent: metrics reported an agent "currently online" as soon as *any* agent had
a fresh heartbeat, and collaboration marked every project agent online
whenever one of them was.
"""

import pytest

from server.core.agent_tracker import AgentTracker
from server.core.database import Database


async def _tracker(tmp_path):
    db = Database(str(tmp_path / "agents.db"))
    await db.connect()
    tracker = AgentTracker(db, lambda *_: None)
    await tracker.initialize()
    return db, tracker


@pytest.mark.asyncio
async def test_metrics_online_does_not_leak_from_other_agents(tmp_path):
    db, tracker = await _tracker(tmp_path)
    try:
        await tracker.agent_connect("cursor", project="atlas")

        online = await tracker.get_agent_metrics("cursor")
        assert online["currently_online"] is True

        # This agent has no session at all; a live stranger must not make it
        # look online.
        stranger = await tracker.get_agent_metrics("claude-code")
        assert stranger["connections"] == 0
        assert stranger["currently_online"] is False
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_metrics_alias_resolves_to_the_same_agent(tmp_path):
    db, tracker = await _tracker(tmp_path)
    try:
        await tracker.agent_connect("claude", project="atlas")

        assert (await tracker.get_agent_metrics("claude"))["currently_online"] is True
        assert (await tracker.get_agent_metrics("claude-code"))["currently_online"] is True
        # A different agent still must not inherit it.
        assert (await tracker.get_agent_metrics("cursor"))["currently_online"] is False
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_collaboration_marks_only_agents_with_a_live_session(tmp_path):
    db, tracker = await _tracker(tmp_path)
    try:
        cursor_id = (await tracker.agent_connect("cursor", project="atlas"))["agent_session_id"]
        vscode_id = (await tracker.agent_connect("vscode", project="atlas"))["agent_session_id"]
        await tracker.agent_disconnect(vscode_id)

        collab = await tracker.get_project_collaboration("atlas")
        online = {a["agent_name"]: a["online"] for a in collab["agents"]}

        assert online["cursor"] is True
        assert online["vscode"] is False
        assert collab["collaboration_score"] == 1
        assert tracker.is_online(cursor_id) is True
    finally:
        await db.close()
