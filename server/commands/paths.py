"""Paths and templates the CLI commands share."""

from __future__ import annotations

import os

from server.core.runtime_config import DEFAULTS as RUNTIME_DEFAULTS



# The repository root. One level deeper than server/cli.py, where this used to
# live, so it needs the extra dirname — server/commands/ -> server/ -> repo.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


MCP_DIR = "mcp"


# Fresh CLI setup deliberately uses the deterministic/offline hash embedder,
# while the runtime resolver's no-config default remains ``auto`` for backward
# compatibility. Once written, this file is the canonical local configuration.
DEFAULT_CONFIG = {
    **RUNTIME_DEFAULTS,
    "embedder_mode": "hash",
}


_HOOK_MARKER = "# levh-hook"


_HOOK_TEMPLATE = """#!/bin/sh
{marker}
# Auto-capture the latest commit message into LEVH.
# Installed by `levh hook install`. Remove with `levh hook uninstall`.
MSG=$(git log -1 --pretty=%B)
HASH=$(git log -1 --pretty=%h)
{python} -m server.cli capture "commit ${{HASH}}: ${{MSG}}" --source git-hook --tags git,commit >/dev/null 2>&1 || true
"""


# Claude Code runs this at the start of every session and puts its stdout into
# the conversation. That is the whole point: the memory arrives without anyone
# asking for it, which is the difference between "I have a memory tool" and
# "the assistant remembers me".
#
# Three properties this script must have, in order of how badly they bite:
#   - it must never fail the session. A memory tool that stops you from
#     starting work is worse than no memory tool, so every path exits 0.
#   - it must be quick. It runs before the session does.
#   - it must print nothing when there is nothing to say, rather than pushing
#     an empty header into every conversation.
_SESSION_HOOK_TEMPLATE = """#!/bin/sh
{marker}
# Injects LEVH's continuity brief at session start.
# Installed by `levh hook install --client claude-code`; remove with
# `levh hook uninstall --client claude-code`.

BRIEF=$({python} -m server.cli continue --limit {limit} --if-any 2>/dev/null) || exit 0
[ -n "$BRIEF" ] || exit 0

printf '%s\n' "$BRIEF"
exit 0
"""
