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
