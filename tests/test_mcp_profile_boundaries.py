"""MCP profile boundary lock (2.25).

Profiles narrow tool *discovery*, nothing more — they are not auth and not a
security boundary (that caveat is asserted to stay in the docs). What IS
contractual: the minimal and work profiles must never advertise destructive
or admin-grade tools, and admin/full must keep them available.
"""

from __future__ import annotations

from server.tools import profiles
from server.tools.profiles import tools_for_profile

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


def test_profiles_stay_cumulative():
    minimal = tools_for_profile("minimal")
    work = tools_for_profile("work")
    admin = tools_for_profile("admin")
    full = tools_for_profile("full")
    assert minimal < work < admin <= full


def test_profile_is_documented_as_not_a_security_boundary():
    """The distinction must stay explicit in the docs: a profile only narrows
    tool discovery; it is not auth, authorization, or a security boundary."""
    import pathlib

    module_doc = (profiles.__doc__ or "").lower()
    readme = pathlib.Path(__file__).resolve().parent.parent / "README.md"
    readme_text = readme.read_text(encoding="utf-8").lower() if readme.exists() else ""
    combined = module_doc + readme_text
    assert "security" in combined and (
        "not a security boundary" in combined
        or "not auth" in combined
        or "not a security" in combined
    ), "docs must state profiles are visibility filtering, not a security boundary"
