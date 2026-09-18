"""Universal Agent Hooks — Auto-connect, auto-session, auto-brief for all agents.

Unlike the Claude Code-only SessionStart hook, this system works with ANY MCP
client by generating agent-specific configuration files that auto-connect to
LEVH when the agent starts.

Supported agents:
  - Claude Code:     .claude/settings.json SessionStart hook
  - Claude Desktop:  claude_desktop_config.json (injected tool)
  - Cursor:          .cursor/mcp.json + .cursorrules
  - VS Code/Cline:   .vscode/mcp.json + settings
  - Windsurf:        .windsurf/mcp.json
  - Generic MCP:     any client that reads mcpServers config

Each agent gets:
  1. Auto-connection to LEVH MCP server
  2. Auto-session creation on first tool use
  3. Continuity brief injection at session start

Recurring checkpoints are a separate feature (`levh checkpoint auto`), not
something these hooks install — the module used to advertise a checkpoint
capability no installer here could produce (#124).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from server.core.env import get_env


# ── Agent hook configurations ────────────────────────────────────────

class AgentHookConfig:
    """Configuration for a specific agent's auto-connect hook."""

    def __init__(
        self,
        name: str,
        display_name: str,
        config_path: str,
        config_format: str = "json",
        supports_session_start: bool = False,
        supports_mcp: bool = True,
    ):
        self.name = name
        self.display_name = display_name
        self.config_path = config_path
        self.config_format = config_format
        self.supports_session_start = supports_session_start
        self.supports_mcp = supports_mcp


SUPPORTED_AGENTS: dict[str, AgentHookConfig] = {
    "claude-code": AgentHookConfig(
        name="claude-code",
        display_name="Claude Code",
        config_path=".claude/settings.json",
        supports_session_start=True,
        supports_mcp=True,
    ),
    "claude-desktop": AgentHookConfig(
        name="claude-desktop",
        display_name="Claude Desktop",
        config_path="claude_desktop_config.json",
        supports_session_start=False,
        supports_mcp=True,
    ),
    "cursor": AgentHookConfig(
        name="cursor",
        display_name="Cursor",
        config_path=".cursor/mcp.json",
        supports_session_start=False,
        supports_mcp=True,
    ),
    "vscode": AgentHookConfig(
        name="vscode",
        display_name="VS Code (Cline)",
        config_path=".vscode/mcp.json",
        supports_session_start=False,
        supports_mcp=True,
    ),
    "windsurf": AgentHookConfig(
        name="windsurf",
        display_name="Windsurf",
        config_path=".windsurf/mcp.json",
        supports_session_start=False,
        supports_mcp=True,
    ),
}


def _resolved_db_path() -> str:
    """Absolute path to the database.

    Resolved through ``get_env`` so the canonical ``LEVH_SQLITE_DB_PATH``
    spelling is honoured too — a bare ``os.getenv`` silently ignored it and
    installed MCP clients pointing at an empty default store (issue #135).
    """
    return os.path.abspath(get_env("SQLITE_DB_PATH", "./stackmemory.db"))


# ── Installers ───────────────────────────────────────────────────────

def install_claude_code_hook(limit: int = 5) -> dict:
    """Install the SessionStart hook for Claude Code."""
    from .hooks import _install_session_hook
    result = _install_session_hook(limit)
    return {"ok": result == 0, "agent": "claude-code"}


def install_cursor_hook(limit: int = 5) -> dict:
    """Install auto-connect for Cursor IDE."""
    result = _install_mcp_json_entry(Path(".cursor/mcp.json"))
    # Cursor also gets a .cursorrules continuity brief.
    _write_cursorrules(Path(".cursorrules"), limit)
    return {**result, "agent": "cursor"}


def install_vscode_hook() -> dict:
    """Install auto-connect for VS Code (Cline extension)."""
    return {**_install_mcp_json_entry(Path(".vscode/mcp.json")), "agent": "vscode"}


def install_windsurf_hook() -> dict:
    """Install auto-connect for Windsurf."""
    return {**_install_mcp_json_entry(Path(".windsurf/mcp.json")), "agent": "windsurf"}


def install_claude_desktop_hook() -> dict:
    """Install auto-connect for Claude Desktop.

    The config is exported to the project root as ``claude_desktop_config_levh.json``;
    the user copies it into Claude Desktop settings.
    """
    return {
        **_install_mcp_json_entry(
            Path("claude_desktop_config_levh.json"),
            note="Copy this config to your Claude Desktop settings",
        ),
        "agent": "claude-desktop",
    }


# ── MCP JSON helpers (shared by the cursor/vscode/windsurf/claude-desktop
# installers and uninstallers) ───────────────────────────────────────


def _install_mcp_json_entry(config_path: Path, *, note: str | None = None) -> dict:
    """Merge the LEVH MCP server entry into a client's ``mcp.json``.

    Existing config is preserved (only the ``levh`` key under ``mcpServers`` is
    upserted), so other MCP servers a user already configured survive.
    """
    config_path.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "command": "levh",
        "args": ["mcp", "stdio"],
        "cwd": os.getcwd(),
        "env": {
            "LEVH_MCP_PROFILE": "work",
            "SQLITE_DB_PATH": _resolved_db_path(),
        },
    }

    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            config = {}
    else:
        config = {}

    config.setdefault("mcpServers", {})["levh"] = entry
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    result = {"ok": True, "config_path": str(config_path)}
    if note:
        result["note"] = note
    return result


def _uninstall_mcp_json_entry(config_path: Path) -> None:
    """Remove only the LEVH MCP server entry from a client's ``mcp.json``."""
    if not config_path.exists():
        return
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config.get("mcpServers", {}).pop("levh", None)
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def _write_cursorrules(path: Path, limit: int = 5) -> None:
    """Write .cursorrules with LEVH continuity brief instructions."""
    content = f"""# LEVH Memory Integration

When starting a new session in this project, run this command to load your memory:
```
levh continue --limit {limit} --if-any
```

This will show you:
- Rules learned from mistakes (never repeat these)
- Pinned memories (always remember)
- Recent sessions and what was worked on
- Active files and recent changes
- Decisions made
- Blockers and TODOs

You can also:
- Store memories: `levh capture "what to remember"`
- Ask your memory: Use the `ask_memory` MCP tool
- Recall memories: Use the `recall_memory` MCP tool
"""
    path.write_text(content, encoding="utf-8")


# ── Unified install ──────────────────────────────────────────────────

def install_universal_hook(
    client: str = "all",
    limit: int = 5,
) -> dict:
    """Install hooks for one or all supported agents.

    Args:
        client: Agent name or "all" for every supported agent.
        limit: Number of sessions to include in the continuity brief.

    Returns:
        Dict mapping agent name to installation result.
    """
    results = {}

    if client == "all":
        agents = list(SUPPORTED_AGENTS.keys())
    else:
        agents = [client] if client in SUPPORTED_AGENTS else [client]

    for agent in agents:
        try:
            if agent == "claude-code":
                results[agent] = install_claude_code_hook(limit)
            elif agent == "cursor":
                results[agent] = install_cursor_hook(limit)
            elif agent == "vscode":
                results[agent] = install_vscode_hook()
            elif agent == "windsurf":
                results[agent] = install_windsurf_hook()
            elif agent == "claude-desktop":
                results[agent] = install_claude_desktop_hook()
            elif agent == "shell":
                results[agent] = install_shell_hook(limit)
            else:
                results[agent] = {"ok": False, "error": f"Unknown agent: {agent}"}
        except Exception as exc:  # noqa: BLE001 - one agent's hook failure must not abort the rest
            results[agent] = {"ok": False, "error": str(exc)}

    return results


def install_shell_hook(limit: int = 5) -> dict:
    """Install an opt-in ``levh brief`` helper for CLI agents.

    Adds a ``levh brief`` shell function (bash/zsh) or ``levh-brief`` function
    (PowerShell) that prints the continuity brief on demand. Unlike a hard
    SessionStart hook it never runs on its own, so it won't spam every new
    shell - a CLI agent wrapper or the user invokes it explicitly:

        levh brief        # bash / zsh
        levh-brief        # PowerShell

    Installing twice is a no-op (idempotent via a marker comment).
    """
    from pathlib import Path

    home = Path.home()
    bash_line = (
        "# LEVH auto-brief helper (opt-in)\n"
        "levh brief() { levh continue --limit %d --if-any 2>/dev/null; }\n" % limit
    )
    ps_line = (
        "# LEVH auto-brief helper (opt-in)\n"
        "function levh-brief { levh continue --limit %d --if-any *> $null }\n" % limit
    )
    marker = "LEVH auto-brief helper"

    updated = []
    targets = [
        ("bash", home / ".bashrc", bash_line),
        ("zsh", home / ".zshrc", bash_line),
        ("powershell", home / "Documents/WindowsPowerShell/Microsoft.PowerShell_profile.ps1", ps_line),
        ("pwsh", home / "Documents/PowerShell/Microsoft.PowerShell_profile.ps1", ps_line),
    ]

    for kind, path, line in targets:
        try:
            if not path.exists():
                continue
            existing = path.read_text(encoding="utf-8", errors="ignore")
            if marker in existing:
                continue
            path.write_text(existing.rstrip() + "\n\n" + line, encoding="utf-8")
            updated.append(kind)
        except OSError:
            continue

    return {
        "ok": True,
        "agent": "shell",
        "updated": updated,
        "commands": ["levh brief", "levh-brief"],
    }


def uninstall_shell_hook() -> dict:
    """Remove any ``levh brief`` helpers previously written to shell profiles."""
    from pathlib import Path

    home = Path.home()
    marker = "LEVH auto-brief helper"
    removed = []
    targets = [
        home / ".bashrc",
        home / ".zshrc",
        home / "Documents/WindowsPowerShell/Microsoft.PowerShell_profile.ps1",
        home / "Documents/PowerShell/Microsoft.PowerShell_profile.ps1",
    ]
    for path in targets:
        try:
            if not path.exists():
                continue
            existing = path.read_text(encoding="utf-8", errors="ignore")
            if marker not in existing:
                continue
            before, sep, _after = existing.partition("# " + marker)
            if sep:
                path.write_text(before.rstrip() + "\n", encoding="utf-8")
                removed.append(str(path))
        except OSError:
            continue
    return {"ok": True, "agent": "shell", "removed": removed}


def uninstall_universal_hook(client: str = "all") -> dict:
    """Uninstall hooks for one or all agents."""
    results = {}

    if client == "all":
        agents = list(SUPPORTED_AGENTS.keys())
    else:
        agents = [client] if client in SUPPORTED_AGENTS else [client]

    for agent in agents:
        try:
            if agent == "claude-code":
                from .hooks import _uninstall_session_hook
                result = _uninstall_session_hook()
                results[agent] = {"ok": result == 0}
            elif agent in ("cursor", "vscode", "windsurf"):
                config_path = {
                    "cursor": ".cursor/mcp.json",
                    "vscode": ".vscode/mcp.json",
                    "windsurf": ".windsurf/mcp.json",
                }[agent]
                _uninstall_mcp_json_entry(Path(config_path))
                results[agent] = {"ok": True}
            elif agent == "shell":
                results[agent] = uninstall_shell_hook()
            else:
                results[agent] = {"ok": False, "error": f"Unknown agent: {agent}"}
        except Exception as exc:  # noqa: BLE001 - one agent's hook failure must not abort the rest
            results[agent] = {"ok": False, "error": str(exc)}

    return results
