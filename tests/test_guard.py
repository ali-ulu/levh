"""Mistake guard — recording a mistake as a rule that outlives the session.

The point of the guard is durability: a rule recorded today must still be
readable weeks later, by a different session, and must reach the next session
through the generated context file. These tests pin that behaviour down.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio

os.environ.setdefault("EMBEDDER_MODE", "hash")

from server.core.guard import GuardService  # noqa: E402
from server.core.memory_engine import MemoryEngine  # noqa: E402
from server.core.types import RULE_TAG  # noqa: E402


@pytest_asyncio.fixture
async def engine(tmp_path):
    eng = MemoryEngine(
        db_path=str(tmp_path / "guard.db"), embedder_mode="hash", short_term_max=50
    )
    await eng.initialize()
    yield eng
    await eng.shutdown()


@pytest_asyncio.fixture
async def guard(engine):
    return GuardService(engine.db, engine)


async def _record(guard, **overrides):
    payload = {
        "task": "write README and commit",
        "wrong_action": "used git commit --no-verify",
        "correct_action": "run git commit normally, with the hooks",
        "root_cause": "tried to go faster by skipping the hooks",
    }
    payload.update(overrides)
    return await guard.record_mistake(**payload)


@pytest.mark.asyncio
async def test_recorded_mistake_becomes_a_pinned_rule(guard, engine):
    result = await _record(guard)

    rule = await engine.get_memory(result["rule_id"])
    assert rule is not None
    # Pinned is the whole mechanism: pinned memories are exempt from decay.
    assert rule.pinned is True
    assert RULE_TAG in rule.tags


@pytest.mark.asyncio
async def test_rule_text_reads_as_an_instruction(guard):
    result = await _record(guard)

    statement = result["statement"]
    assert statement.startswith("Do not used git commit --no-verify.")
    assert "Instead: run git commit normally, with the hooks." in statement
    assert "Root cause: tried to go faster by skipping the hooks." in statement


@pytest.mark.asyncio
async def test_the_incident_is_logged_alongside_the_rule(guard):
    result = await _record(guard, tool_name="Bash", severity="high")

    rows = await guard.list_violations()
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == result["violation_id"]
    assert row["rule_id"] == result["rule_id"]
    assert row["tool_name"] == "Bash"
    assert row["severity"] == "high"
    assert row["resolved"] == 0


@pytest.mark.asyncio
async def test_severity_raises_the_rule_importance(guard, engine):
    low = await _record(guard, severity="low", wrong_action="left a TODO in")
    critical = await _record(guard, severity="critical", wrong_action="dropped the prod table")

    low_rule = await engine.get_memory(low["rule_id"])
    critical_rule = await engine.get_memory(critical["rule_id"])
    assert critical_rule.importance > low_rule.importance


@pytest.mark.asyncio
async def test_unknown_severity_falls_back_to_medium(guard):
    result = await _record(guard, severity="catastrophic")
    assert result["severity"] == "medium"


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["wrong_action", "correct_action"])
async def test_a_rule_without_both_halves_is_rejected(guard, field):
    with pytest.raises(ValueError):
        await _record(guard, **{field: "   "})


@pytest.mark.asyncio
async def test_violations_can_be_filtered_by_severity(guard):
    await _record(guard, severity="low", wrong_action="a")
    await _record(guard, severity="critical", wrong_action="b")

    critical = await guard.list_violations(severity="critical")
    assert [r["wrong_action"] for r in critical] == ["b"]


@pytest.mark.asyncio
async def test_rules_are_listed_most_important_first(guard):
    await _record(guard, severity="low", wrong_action="a")
    await _record(guard, severity="critical", wrong_action="b")

    rules = await guard.list_rules()
    assert len(rules) == 2
    assert rules[0].content.startswith("Do not b.")


@pytest.mark.asyncio
async def test_a_rule_survives_a_reopened_database(tmp_path):
    """The rule must outlive the process that recorded it — that is the point."""
    db_path = str(tmp_path / "persist.db")

    first = MemoryEngine(db_path=db_path, embedder_mode="hash")
    await first.initialize()
    result = await GuardService(first.db, first).record_mistake(
        task="deploy",
        wrong_action="deployed straight to prod",
        correct_action="deploy to staging first",
    )
    await first.shutdown()

    second = MemoryEngine(db_path=db_path, embedder_mode="hash")
    await second.initialize()
    try:
        guard = GuardService(second.db, second)
        rules = await guard.list_rules()
        assert [r.id for r in rules] == [result["rule_id"]]
        assert len(await guard.list_violations()) == 1
    finally:
        await second.shutdown()


@pytest.mark.asyncio
async def test_rules_lead_the_generated_context_file(guard, engine):
    await engine.store("Team standup is at 10:00", pinned=True, memory_type="episodic")
    await _record(guard)

    content = await engine.generate_context_file()

    assert "## Rules Learned From Mistakes" in content
    assert "Do not used git commit --no-verify." in content
    # Rules come before the ordinary pinned notes, and are not printed twice.
    assert content.index("## Rules Learned From Mistakes") < content.index(
        "## Always Remember (pinned)"
    )
    assert content.count("Do not used git commit --no-verify.") == 1
    assert "Team standup is at 10:00" in content


@pytest.mark.asyncio
async def test_context_file_is_unchanged_when_no_mistakes_are_recorded(engine):
    await engine.store("Team standup is at 10:00", pinned=True, memory_type="episodic")

    content = await engine.generate_context_file()

    assert "## Rules Learned From Mistakes" not in content
    assert "Team standup is at 10:00" in content


@pytest.mark.asyncio
async def test_a_project_scoped_rule_stays_in_its_project(guard, engine):
    await _record(guard, project="levh")

    assert "Do not used git commit" in await engine.generate_context_file(project="levh")
    assert "Do not used git commit" not in await engine.generate_context_file(project="other")
