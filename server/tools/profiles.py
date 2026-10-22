"""MCP tool profiles — control how many tools a client actually sees.

Advertising all 74 tools to every AI client hurts tool-selection accuracy:
selection quality falls off as the tool list grows, so a public demo where the
agent must pick the right tool from 61 is a reliability risk. Profiles let a
client mount a focused subset instead.

Every tool is assigned to the *smallest* profile it belongs to; the size
profiles are cumulative in the order minimal ⊂ work ⊂ admin ⊂ full:

    minimal — the core capture/recall loop an agent needs and nothing else.
    work    — minimal + the daily-driver surface (briefing, meeting prep,
              entities, trust, conflicts) for everyday memory use.
    admin   — work + maintenance/management (backup, dedupe, redaction, review,
              trust/conflict recompute, sessions, import/export).
    full    — every tool, including connectors and niche helpers.

The size profiles are a tool-discovery filter, NOT a security boundary: they
narrow which tools a client *sees*, they are not auth or authorization, and
they must never be relied on to protect data. Their one hard guarantee (locked
by ``tests/test_mcp_profile_boundaries.py``) is that minimal/work never
*advertise* destructive admin tools (backup restore, purge/forget, redaction
admin).

``read_only`` is deliberately different. It is a permission profile for
external/cloud agents that may read memory but must never write it:

    read_only — an explicit allowlist of recall/search/context/metadata reads.
                It is enforced server-side at tool-call time (see
                :mod:`server.tools.access`), so a client cannot reach a write
                path by invoking a tool it was not advertised. It is not part
                of the cumulative size ladder, because "fewer tools" and
                "no writes" are different questions.

The generated MCP config defaults to ``work``; ``full`` stays available but is
opt-in. The stdio and SSE transports use the same default. This module is pure
data + pure functions — no MCP imports — so it is trivially testable and can't
drift from the registry silently (a test asserts the access classification and
the tiers cover exactly the registered tool set).
"""

from __future__ import annotations

# Ordered from smallest to largest. A profile includes every tool tiered at or
# below it in this order.
PROFILE_ORDER: tuple[str, ...] = ("minimal", "work", "admin", "full")
DEFAULT_PROFILE = "work"

#: The permission profile, kept out of the size ladder on purpose: a client can
#: want the full read surface without gaining a single write.
READ_ONLY_PROFILE = "read_only"

#: Every profile name ``resolve_profile`` accepts, permission profile first.
ALL_PROFILES: tuple[str, ...] = (READ_ONLY_PROFILE, *PROFILE_ORDER)

# tool name → the smallest profile tier that first includes it.
TOOL_TIERS: dict[str, str] = {
    # ── minimal: the core capture/recall loop ────────────────────────
    "store_memory": "minimal",
    "recall_memory": "minimal",
    "search_memory": "minimal",
    "get_context": "minimal",
    "get_memory_stats": "minimal",
    "get_continuity_brief": "minimal",  # Call at session start
    # ── work: everyday memory use ────────────────────────────────────
    "list_memories": "work",
    "ask_memory": "work",
    "reinforce_memory": "work",
    "pin_memory": "work",
    "attach_file": "work",
    "briefing": "work",
    "meeting_prep": "work",
    "list_entities": "work",
    "about_entity": "work",
    "memory_trust": "work",
    "list_conflict_candidates": "work",
    # A rule only helps if it is recorded when the mistake is fresh, which is
    # during ordinary work — hence "work" rather than "admin". Reading the log
    # back is a review activity, so it sits one tier up.
    "record_mistake": "work",
    # The gate runs in front of a tool call, so it has to be available during
    # ordinary work too; a rule that can only be consulted in an admin session
    # is consulted too late.
    "check_action": "work",
    # ── admin: maintenance / management ──────────────────────────────
    "unpin_memory": "admin",
    "update_memory": "admin",
    "set_importance": "admin",
    "forget_memory": "admin",
    "memory_feedback": "admin",
    "related_memories": "admin",
    "timeline": "admin",
    "list_projects": "admin",
    "list_sources": "admin",
    "list_people": "admin",
    "about_person": "admin",
    "list_organizations": "admin",
    "about_organization": "admin",
    "list_decisions": "admin",
    "list_fading_memories": "admin",
    "list_low_trust_memories": "admin",
    "list_review_memories": "admin",
    "review_memory": "admin",
    "create_session": "admin",
    "end_session": "admin",
    "summarize_session": "admin",
    "consolidate_memories": "admin",
    "consolidate_similar": "admin",
    "clear_short_term": "admin",
    "dedupe_memories": "admin",
    "generate_context_file": "admin",
    "export_memories": "admin",
    "import_memories": "admin",
    "create_backup": "admin",
    "restore_backup": "admin",
    "reindex_entities": "admin",
    "recompute_trust_scores": "admin",
    "detect_conflict_candidates": "admin",
    "review_conflict_candidate": "admin",
    "list_mistakes": "admin",
    "evaluate_admission": "admin",
    "admit_memory": "admin",
    "audit_secrets": "admin",
    "redact_secrets": "admin",
    "purge_memory": "admin",
    # ── work: agent tracking & checkpoints (daily driver visibility) ──
    "agent_connect": "work",
    "agent_heartbeat": "work",
    "agent_disconnect": "work",
    "create_checkpoint": "work",
    "list_agent_activity": "work",
    "list_checkpoints": "work",
    "get_agent_stats": "work",
    "agent_metrics": "work",
    "usage_billing": "work",
    "project_collaboration": "work",
    # ── full: connectors + niche helpers ─────────────────────────────
    "list_connectors": "full",
    "get_connector_help": "full",
    "import_from_app": "full",
    "sync_connector": "full",
    "connector_sync_status": "full",
}

#: The only tools the ``read_only`` profile advertises and the only tools its
#: server-side guard will execute. Explicit, not derived from the size tiers:
#: "fewer tools" and "no writes" are different questions, and a read tool that
#: happens to be admin-tier is still a read.
READ_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        "recall_memory",
        "search_memory",
        "get_context",
        "get_memory_stats",
        "get_continuity_brief",
        "list_memories",
        "ask_memory",
        "briefing",
        "meeting_prep",
        "list_entities",
        "about_entity",
        "memory_trust",
        "list_conflict_candidates",
        "check_action",
        "related_memories",
        "timeline",
        "list_projects",
        "list_sources",
        "list_people",
        "about_person",
        "list_organizations",
        "about_organization",
        "list_decisions",
        "list_fading_memories",
        "list_low_trust_memories",
        "list_review_memories",
        "list_mistakes",
        "list_agent_activity",
        "list_checkpoints",
        "get_agent_stats",
        "agent_metrics",
        "usage_billing",
        "project_collaboration",
        "list_connectors",
        "get_connector_help",
        "connector_sync_status",
    }
)

#: Tools that create, update, or delete stored state. A read-only profile must
#: never expose one; ``tests/test_mcp_profile_boundaries.py`` asserts the two
#: sets stay disjoint and that every registered tool is classified.
MUTATING_TOOLS: frozenset[str] = frozenset(
    {
        "store_memory",
        "reinforce_memory",
        "pin_memory",
        "attach_file",
        "record_mistake",
        "unpin_memory",
        "update_memory",
        "set_importance",
        "forget_memory",
        "memory_feedback",
        "review_memory",
        "create_session",
        "end_session",
        "summarize_session",
        "consolidate_memories",
        "consolidate_similar",
        "clear_short_term",
        "dedupe_memories",
        "generate_context_file",
        "import_memories",
        "create_backup",
        "restore_backup",
        "reindex_entities",
        "recompute_trust_scores",
        "detect_conflict_candidates",
        "review_conflict_candidate",
        "admit_memory",
        "redact_secrets",
        "purge_memory",
        "agent_connect",
        "agent_heartbeat",
        "agent_disconnect",
        "create_checkpoint",
        "import_from_app",
        "sync_connector",
    }
)

#: Reads that still stay off the external allowlist: bulk export, secret
#: scanning, and the admission preview are admin/security surfaces, not the
#: recall/context/metadata reads a read-only agent is meant to have. Named
#: separately so the classification is honest instead of calling a read a write.
ADMIN_READ_TOOLS: frozenset[str] = frozenset(
    {"export_memories", "audit_secrets", "evaluate_admission"}
)


class UnknownProfileError(ValueError):
    """Raised when a profile name is not one of ALL_PROFILES."""


def resolve_profile(name: str | None) -> str:
    """Normalize a profile name. ``None``/empty → DEFAULT_PROFILE. Raises
    ``UnknownProfileError`` on an unrecognized name (case-insensitive)."""
    if not name:
        return DEFAULT_PROFILE
    normalized = name.strip().lower()
    if normalized not in ALL_PROFILES:
        raise UnknownProfileError(
            f"unknown MCP profile '{name}'. Valid: {', '.join(ALL_PROFILES)}"
        )
    return normalized


def tools_for_profile(name: str | None) -> set[str]:
    """Return the set of tool names a profile exposes. ``full`` returns every
    known tool; ``read_only`` returns its explicit allowlist."""
    profile = resolve_profile(name)
    if profile == READ_ONLY_PROFILE:
        return set(READ_ONLY_TOOLS)
    cutoff = PROFILE_ORDER.index(profile)
    return {
        tool
        for tool, tier in TOOL_TIERS.items()
        if PROFILE_ORDER.index(tier) <= cutoff
    }


def profile_counts() -> dict[str, int]:
    """{profile: tool_count} for every profile, permission profile first."""
    return {p: len(tools_for_profile(p)) for p in ALL_PROFILES}
