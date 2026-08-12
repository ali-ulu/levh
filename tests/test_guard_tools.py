"""The guard's MCP tools — registration, profile tiering, and returned text."""

from __future__ import annotations

import os

import pytest
import pytest_asyncio

os.environ.setdefault("EMBEDDER_MODE", "hash")

from mcp.server.fastmcp import FastMCP  # noqa: E402

from server.core.memory_engine import MemoryEngine  # noqa: E402
from server.tools.profiles import TOOL_TIERS, tools_for_profile  # noqa: E402
from server.tools.register import register_all_tools  # noqa: E402


@pytest_asyncio.fixture
async def engine(tmp_path):
    eng = MemoryEngine(
        db_path=str(tmp_path / "tools.db"), embedder_mode="hash", short_term_max=50
    )
    await eng.initialize()
    yield eng
    await eng.shutdown()


@pytest.mark.asyncio
async def test_both_tools_register(engine):
    mcp = FastMCP("test")
    register_all_tools(mcp, engine, profile="full")
    names = {t.name for t in await mcp.list_tools()}

    assert "record_mistake" in names
    assert "list_mistakes" in names


def test_recording_is_available_during_ordinary_work():
    """A rule is only recorded if the tool is visible in the default profile."""
    assert TOOL_TIERS["record_mistake"] == "work"
    assert "record_mistake" in tools_for_profile("work")


def test_reading_the_log_back_is_an_admin_activity():
    assert "list_mistakes" not in tools_for_profile("work")
    assert "list_mistakes" in tools_for_profile("admin")


async def _call(mcp: FastMCP, name: str, args: dict) -> str:
    result = await mcp.call_tool(name, args)
    blocks = result[0] if isinstance(result, tuple) else result
    return "\n".join(getattr(b, "text", "") for b in blocks)


@pytest.mark.asyncio
async def test_record_mistake_reports_the_rule_it_created(engine):
    mcp = FastMCP("test")
    register_all_tools(mcp, engine, profile="full")

    text = await _call(
        mcp,
        "record_mistake",
        {
            "task": "ship the release",
            "wrong_action": "skipped the test suite",
            "correct_action": "run pytest before tagging",
            "severity": "high",
        },
    )

    assert "pinned rule" in text
    assert "severity: high" in text
    assert "Do not skipped the test suite." in text


@pytest.mark.asyncio
async def test_list_mistakes_reports_an_empty_log_plainly(engine):
    mcp = FastMCP("test")
    register_all_tools(mcp, engine, profile="full")

    assert "No mistakes recorded." in await _call(mcp, "list_mistakes", {})


@pytest.mark.asyncio
async def test_list_mistakes_shows_what_was_recorded(engine):
    mcp = FastMCP("test")
    register_all_tools(mcp, engine, profile="full")

    await _call(
        mcp,
        "record_mistake",
        {
            "task": "ship the release",
            "wrong_action": "skipped the test suite",
            "correct_action": "run pytest before tagging",
            "severity": "high",
        },
    )
    text = await _call(mcp, "list_mistakes", {})

    assert "1 mistake(s) on record" in text
    assert "[high]" in text
    assert "skipped the test suite" in text


@pytest.mark.asyncio
async def test_an_unusable_mistake_is_refused_with_a_reason(engine):
    mcp = FastMCP("test")
    register_all_tools(mcp, engine, profile="full")

    text = await _call(
        mcp,
        "record_mistake",
        {"task": "x", "wrong_action": "did a thing", "correct_action": ""},
    )

    assert "Could not record the mistake" in text
    assert "correct_action is required" in text


@pytest.mark.asyncio
async def test_an_unknown_severity_filter_is_reported(engine):
    mcp = FastMCP("test")
    register_all_tools(mcp, engine, profile="full")

    text = await _call(mcp, "list_mistakes", {"severity": "spicy"})
    assert "Unknown severity" in text
