"""MCP tool profiles — control how many tools a client actually sees.

Advertising all 62 tools to every AI client hurts tool-selection accuracy:
selection quality falls off as the tool list grows, so a public demo where the
agent must pick the right tool from 61 is a reliability risk. Profiles let a
client mount a focused subset instead.

Every tool is assigned to the *smallest* profile it belongs to; profiles are
cumulative in the order minimal ⊂ work ⊂ admin ⊂ full:

    minimal — the core capture/recall loop an agent needs and nothing else.
    work    — minimal + the daily-driver surface (briefing, meeting prep,
              entities, trust, conflicts) for everyday memory use.
    admin   — work + maintenance/management (backup, dedupe, redaction, review,
              trust/conflict recompute, sessions, import/export).
    full    — every tool, including connectors and niche helpers.

A profile is a tool-discovery filter, NOT a security boundary: it narrows
which tools a client *sees*, it is not auth or authorization, and it must
never be relied on to protect data. Its one hard guarantee (locked by
``tests/test_mcp_profile_boundaries.py``) is that minimal/work never
*advertise* destructive admin tools (backup restore, purge/forget,
redaction admin).

The generated MCP config defaults to ``work``; ``full`` stays available but is
opt-in. This module is pure data + pure functions — no MCP imports — so it is
trivially testable and can't drift from the registry silently (a test asserts
the tiers cover exactly the registered tool set).
"""

from __future__ import annotations

# Ordered from smallest to largest. A profile includes every tool tiered at or
# below it in this order.
PROFILE_ORDER: tuple[str, ...] = ("minimal", "work", "admin", "full")
DEFAULT_PROFILE = "work"

# tool name → the smallest profile tier that first includes it.
TOOL_TIERS: dict[str, str] = {
    # ── minimal: the core capture/recall loop ────────────────────────
    "store_memory": "minimal",
    "recall_memory": "minimal",
    "search_memory": "minimal",
    "get_context": "minimal",
    "get_memory_stats": "minimal",
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


class UnknownProfileError(ValueError):
    """Raised when a profile name is not one of PROFILE_ORDER."""


def resolve_profile(name: str | None) -> str:
    """Normalize a profile name. ``None``/empty → DEFAULT_PROFILE. Raises
    ``UnknownProfileError`` on an unrecognized name (case-insensitive)."""
    if not name:
        return DEFAULT_PROFILE
    normalized = name.strip().lower()
    if normalized not in PROFILE_ORDER:
        raise UnknownProfileError(
            f"unknown MCP profile '{name}'. Valid: {', '.join(PROFILE_ORDER)}"
        )
    return normalized


def tools_for_profile(name: str | None) -> set[str]:
    """Return the set of tool names a profile exposes. ``full`` returns every
    known tool."""
    profile = resolve_profile(name)
    cutoff = PROFILE_ORDER.index(profile)
    return {
        tool
        for tool, tier in TOOL_TIERS.items()
        if PROFILE_ORDER.index(tier) <= cutoff
    }


def profile_counts() -> dict[str, int]:
    """{profile: tool_count} for every profile, smallest first."""
    return {p: len(tools_for_profile(p)) for p in PROFILE_ORDER}
