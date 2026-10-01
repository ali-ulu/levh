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

from server.core.guard import GuardService
from server.core.memory_engine import MemoryEngine
from server.core.types import RULE_TAG


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


# ── Pre-action gate (#337) ────────────────────────────────────────────
#
# The gate reads the same rules `record_mistake` writes, so these tests drive
# the real service end to end: record a mistake, then judge an action against
# it. The unit-level false-positive story lives in tests/test_action_gate.py;
# what is checked here is that the wiring preserves it.


@pytest.mark.asyncio
async def test_a_recorded_mistake_warns_a_matching_action(guard):
    await _record(guard, task="commit the README", wrong_action="used git commit --no-verify")

    verdict = await guard.check_action("Bash", "git commit --no-verify -m 'wip'")

    assert verdict["decision"] == "warn"
    assert verdict["checked_rules"] == 1
    assert verdict["matched_rules"][0]["severity"] == "medium"
    assert "no-verify" in verdict["matched_rules"][0]["statement"]


@pytest.mark.asyncio
async def test_an_unrelated_action_is_allowed(guard):
    await _record(guard, task="commit the README", wrong_action="used git commit --no-verify")

    verdict = await guard.check_action("Bash", "pytest -q tests/")

    assert verdict["decision"] == "allow"
    assert verdict["matched_rules"] == []
    # It still looked — "allow" means "checked and nothing matched".
    assert verdict["checked_rules"] == 1


@pytest.mark.asyncio
async def test_the_gate_is_read_only(engine, guard):
    """Asking must not change the answer. The gate runs in front of every tool
    call, so a gate that mutated the store would corrupt the signal it reads."""
    await _record(guard, task="commit the README", wrong_action="used git commit --no-verify")
    before = await engine.get_stats()

    await guard.check_action("Bash", "git commit --no-verify -m 'wip'")
    await guard.check_action("Bash", "pytest -q")

    after = await engine.get_stats()
    assert after.total_memories == before.total_memories
    assert after.pinned_count == before.pinned_count


@pytest.mark.asyncio
async def test_no_rules_yet_allows_with_a_reason_that_says_so(guard):
    verdict = await guard.check_action("Bash", "git push --force origin main")

    assert verdict["decision"] == "allow"
    assert verdict["checked_rules"] == 0
    assert "no rules recorded" in verdict["reason"]


@pytest.mark.asyncio
async def test_verdict_echoes_what_it_judged(guard):
    await _record(guard)
    verdict = await guard.check_action("Bash", "git commit --no-verify", project="levh")

    assert verdict["tool_name"] == "Bash"
    assert verdict["project"] == "levh"


@pytest.mark.asyncio
async def test_a_global_rule_warns_a_project_scoped_check(guard):
    """A rule recorded without a project is the *most* general kind, so it must
    reach a project-scoped caller. `search_memories` filters `project = ?`
    exactly, so without the merge a global rule would be invisible here — the
    gate would return `allow` for the one mistake that applies everywhere."""
    await _record(
        guard,
        task="push the release branch",
        wrong_action="used git push --force origin main",
        project=None,
    )

    verdict = await guard.check_action(
        "Bash", "git push --force origin main", project="levh"
    )

    assert verdict["decision"] == "warn"
    assert verdict["checked_rules"] == 1


@pytest.mark.asyncio
async def test_a_rule_from_another_project_is_not_merged_in(guard):
    """Merging global rules must not merge *every* rule: a rule recorded for a
    different project stays out of a project-scoped check."""
    await _record(
        guard,
        task="push the release branch",
        wrong_action="used git push --force origin main",
        project="other-project",
    )

    verdict = await guard.check_action(
        "Bash", "git push --force origin main", project="levh"
    )

    assert verdict["decision"] == "allow"
    assert verdict["checked_rules"] == 0


@pytest.mark.asyncio
async def test_strict_project_scope_can_exclude_global_rules(guard):
    await _record(
        guard,
        task="push the release branch",
        wrong_action="used git push --force origin main",
        project=None,
    )

    rules = await guard.list_rules(project="levh", include_global=False)

    assert rules == []


@pytest.mark.asyncio
async def test_unrelated_pinned_memories_cannot_starve_the_global_rule(guard):
    """The scope filter has to run before the query's LIMIT.

    ``search_memories`` returns a page of at most ``limit`` rows, and the guard
    only keeps the ones carrying ``RULE_TAG``. If the global merge were applied
    to that page in Python instead of in the query, a flood of pinned memories
    from other projects — which are not rules — would fill the page and the one
    global rule that applies would never reach ``check_action``, which would
    then return ``allow`` for a mistake recorded everywhere.

    Pinned rows sort first, so without SQL-level scoping these 200 crowd the
    global rule out of every page the default limit can afford.
    """
    await _record(
        guard,
        task="push the release branch",
        wrong_action="used git push --force origin main",
        project=None,
    )
    for i in range(200):
        await guard.engine.store(
            f"pinned note from another project {i}",
            memory_type="episodic",
            project="other-project",
            pinned=True,
            importance=0.9,
        )

    verdict = await guard.check_action(
        "Bash", "git push --force origin main", project="levh"
    )

    assert verdict["decision"] == "warn"
    assert verdict["checked_rules"] == 1

