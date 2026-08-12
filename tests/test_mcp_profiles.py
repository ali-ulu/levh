"""MCP tool-profile tests — the surface-control gate.

Profiles keep the advertised tool count small so a client's tool-selection
accuracy stays high. These tests lock the tier map to the *actual* registered
tool set (so a new tool can't silently escape a profile), verify the cumulative
subset relation, and confirm the register-time filter advertises exactly the
right tools for each profile.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine
from server.tools import profiles
from server.tools.profiles import (
    DEFAULT_PROFILE,
    PROFILE_ORDER,
    TOOL_TIERS,
    UnknownProfileError,
    profile_counts,
    resolve_profile,
    tools_for_profile,
)
from server.tools.register import register_all_tools


# ── pure profile logic ────────────────────────────────────────────
def test_counts_are_the_expected_bands():
    # Small minimal, tight work, broad admin, complete full.
    assert profile_counts() == {"minimal": 5, "work": 16, "admin": 56, "full": 61}


def test_default_profile_is_work():
    assert DEFAULT_PROFILE == "work"
    assert resolve_profile(None) == "work"
    assert resolve_profile("") == "work"


def test_profiles_are_cumulative_subsets():
    sets = [tools_for_profile(p) for p in PROFILE_ORDER]
    for smaller, larger in zip(sets, sets[1:]):
        assert smaller < larger, "each profile must strictly contain the previous"


def test_full_is_every_known_tool():
    assert tools_for_profile("full") == set(TOOL_TIERS)


def test_every_tier_value_is_a_real_profile():
    assert set(TOOL_TIERS.values()) <= set(PROFILE_ORDER)


def test_resolve_profile_is_case_insensitive_and_strtrimmed():
    assert resolve_profile("  ADMIN ") == "admin"


def test_unknown_profile_raises():
    with pytest.raises(UnknownProfileError):
        resolve_profile("gigantic")
    with pytest.raises(UnknownProfileError):
        tools_for_profile("gigantic")


def test_minimal_is_the_core_loop():
    assert tools_for_profile("minimal") == {
        "store_memory",
        "recall_memory",
        "search_memory",
        "get_context",
        "get_memory_stats",
    }


# ── register-time filtering (integration) ─────────────────────────
@pytest_asyncio.fixture
async def engine(tmp_path):
    eng = MemoryEngine(
        db_path=str(tmp_path / "prof.db"), embedder_mode="hash", short_term_max=50
    )
    await eng.initialize()
    try:
        yield eng
    finally:
        await eng.shutdown()


async def _advertised(mcp: FastMCP) -> set[str]:
    return {t.name for t in await mcp.list_tools()}


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", list(PROFILE_ORDER))
async def test_register_advertises_exactly_the_profile(engine, profile):
    mcp = FastMCP("test")
    returned = register_all_tools(mcp, engine, profile=profile)
    advertised = await _advertised(mcp)
    assert set(returned) == advertised
    assert advertised == tools_for_profile(profile)


@pytest.mark.asyncio
async def test_full_registration_has_no_drift(engine):
    """Every decorated tool must be accounted for in the tier map — otherwise a
    non-full profile would silently drop it."""
    mcp = FastMCP("test")
    register_all_tools(mcp, engine, profile="full")
    advertised = await _advertised(mcp)
    assert advertised == set(TOOL_TIERS)
    assert len(advertised) == 61


@pytest.mark.asyncio
async def test_bare_registration_is_full_for_backward_compat(engine):
    """A no-arg call must still expose every tool — pre-profile callers and the
    server's unset-env path rely on this. Profile-limiting is opt-in."""
    mcp = FastMCP("test")
    register_all_tools(mcp, engine)  # no profile → full
    assert await _advertised(mcp) == tools_for_profile("full")


# ── generated config carries the profile ──────────────────────────
def test_generated_config_defaults_to_work_profile():
    from server.configs import generate_config

    cfg = generate_config("cursor", project_path=".")
    env = cfg["mcpServers"]["levh"]["env"]
    assert env["LEVH_MCP_PROFILE"] == "work"


def test_generated_config_honors_explicit_profile():
    from server.configs import generate_config

    cfg = generate_config("cursor", project_path=".", profile="full")
    assert cfg["mcpServers"]["levh"]["env"]["LEVH_MCP_PROFILE"] == "full"
