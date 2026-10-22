"""MCP profile boundary lock (2.25).

The size profiles narrow tool *discovery*, nothing more — they are not auth and
not a security boundary (that caveat is asserted to stay in the docs). What IS
contractual: the minimal and work profiles must never advertise destructive or
admin-grade tools, admin/full must keep them available, and the separate
``read_only`` permission profile must expose none of the mutating tools.
"""

from __future__ import annotations

import pathlib

from server.tools import profiles
from server.tools.profiles import (
    ADMIN_READ_TOOLS,
    MUTATING_TOOLS,
    READ_ONLY_PROFILE,
    READ_ONLY_TOOLS,
    TOOL_TIERS,
    tools_for_profile,
)

# Tools that can destroy or exfiltrate stored memory wholesale — the exact
# surface the task requires minimal/work to exclude.
DESTRUCTIVE_ADMIN_TOOLS = {
    "forget_memory",
    "purge_memory",
    "redact_secrets",
    "audit_secrets",
    "restore_backup",
    "create_backup",
    "dedupe_memories",
    "clear_short_term",
    "import_memories",
    "export_memories",
    "update_memory",
    "set_importance",
}


def test_minimal_profile_excludes_destructive_admin_tools():
    assert tools_for_profile("minimal").isdisjoint(DESTRUCTIVE_ADMIN_TOOLS)


def test_work_profile_excludes_destructive_admin_tools():
    assert tools_for_profile("work").isdisjoint(DESTRUCTIVE_ADMIN_TOOLS)


def test_work_profile_excludes_backup_restore_and_hard_delete_specifically():
    work = tools_for_profile("work")
    for tool in ("restore_backup", "create_backup", "purge_memory", "forget_memory",
                 "redact_secrets"):
        assert tool not in work, f"{tool} must not be advertised below admin"


def test_admin_and_full_profiles_include_management_tools():
    admin = tools_for_profile("admin")
    full = tools_for_profile("full")
    assert DESTRUCTIVE_ADMIN_TOOLS <= admin
    assert DESTRUCTIVE_ADMIN_TOOLS <= full


def test_read_only_excludes_destructive_admin_tools():
    assert not (READ_ONLY_TOOLS & DESTRUCTIVE_ADMIN_TOOLS)


def test_read_only_excludes_every_mutating_tool():
    """The permission profile's whole point: no write path is reachable."""
    assert READ_ONLY_TOOLS.isdisjoint(MUTATING_TOOLS)
    assert not (READ_ONLY_TOOLS & ADMIN_READ_TOOLS)
    assert not (MUTATING_TOOLS & ADMIN_READ_TOOLS)


def test_read_only_keeps_the_intended_reads():
    for tool in (
        "recall_memory",
        "search_memory",
        "get_context",
        "get_memory_stats",
        "get_continuity_brief",
        "list_memories",
        "ask_memory",
    ):
        assert tool in READ_ONLY_TOOLS, tool


def test_read_only_never_advertises_a_write():
    exposed = tools_for_profile(READ_ONLY_PROFILE)
    assert exposed == set(READ_ONLY_TOOLS)
    assert exposed.isdisjoint(MUTATING_TOOLS)
    assert "store_memory" not in exposed


def test_every_registered_tool_is_classified():
    """A new tool cannot silently escape the read-only debate: it must be an
    allowed read, a mutating tool, or an explicitly excluded admin read."""
    classified = READ_ONLY_TOOLS | MUTATING_TOOLS | ADMIN_READ_TOOLS
    assert classified == set(TOOL_TIERS)


def test_profiles_stay_cumulative():
    minimal = tools_for_profile("minimal")
    work = tools_for_profile("work")
    admin = tools_for_profile("admin")
    full = tools_for_profile("full")
    assert minimal < work < admin <= full


def test_profile_is_documented_with_an_accurate_threat_boundary():
    """The distinction must stay explicit: the size profiles only narrow tool
    discovery, while read_only is enforced server-side at call time."""
    module_doc = (profiles.__doc__ or "").lower()
    readme = pathlib.Path(__file__).resolve().parent.parent / "README.md"
    readme_text = readme.read_text(encoding="utf-8").lower() if readme.exists() else ""
    combined = module_doc + readme_text
    assert "read_only" in combined
    assert "server-side" in combined or "server side" in combined
    assert "not a security boundary" in combined or "not auth" in combined
