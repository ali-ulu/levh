"""Multi-Platform MCP Config Generator.

Generates MCP configuration JSON for each AI-coding platform.
All platforms use stdio transport with ``python -m server.mcp_stdio``.

Usage (CLI):
    python -m server.configs --output ./configs --project /path/to/stackmemory

Usage (programmatic):
    from server.configs import generate_config, generate_all_configs
    cfg = generate_config("claude_desktop", project_path="/path/to/stackmemory")
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.tools.profiles import DEFAULT_PROFILE, resolve_profile
from server.core.runtime_config import resolve_runtime_config, runtime_env

# ── Platform definitions ────────────────────────────────────────────

PLATFORMS: dict[str, dict[str, Any]] = {
    "claude_desktop": {
        "file_name": "claude_desktop_config.json",
        "file_path": None,  # OS-specific; user chooses
        "description": "Claude Desktop (Anthropic)",
    },
    "cursor": {
        "file_name": "mcp.json",
        "file_path": ".cursor/mcp.json",
        "description": "Cursor IDE",
    },
    "claude_code": {
        "file_name": ".claude.json",
        "file_path": ".claude.json",
        "description": "Claude Code (CLI)",
    },
    "vscode": {
        "file_name": "mcp.json",
        "file_path": ".vscode/mcp.json",
        "description": "VS Code (with Cline extension)",
    },
    "windsurf": {
        "file_name": "mcp.json",
        "file_path": ".windsurf/mcp.json",
        "description": "Windsurf",
    },
    "cline": {
        "file_name": "mcp.json",
        "file_path": ".vscode/mcp.json",
        "description": "Cline (VS Code extension)",
    },
}

PLATFORM_ALIASES: dict[str, str] = {
    "claude": "claude_desktop",
    "claude_desktop": "claude_desktop",
    "cursor": "cursor",
    "windsurf": "windsurf",
    "claude_code": "claude_code",
    "vscode": "vscode",
    "cline": "cline",
    "generic": "claude_desktop",
}


def normalize_platform(name: str) -> str:
    """Return the authoritative platform key for a public alias."""
    normalized = PLATFORM_ALIASES.get((name or "").strip().lower(), "")
    if not normalized or normalized not in PLATFORMS:
        available = ", ".join(sorted(PLATFORM_ALIASES))
        raise ValueError(f"Unknown platform '{name}'. Available: {available}")
    return normalized


def _build_server_entry(
    project_path: str,
    profile: str = DEFAULT_PROFILE,
    **env_overrides: str,
) -> dict[str, Any]:
    """Build the inner server config dict shared by all platforms.

    ``profile`` sets STACKMEMORY_MCP_PROFILE so the client advertises a focused
    tool surface (default ``work``) instead of all 59 tools — better
    tool-selection accuracy. Pass ``full`` to opt back into everything.
    """
    abs_project = os.path.abspath(project_path)
    runtime = resolve_runtime_config(cwd=abs_project)
    env: dict[str, str] = {
        **runtime_env(runtime),
        "STACKMEMORY_MCP_PROFILE": resolve_profile(profile),
    }
    env.update({k: str(v) for k, v in env_overrides.items()})

    # Determine the Python executable to use
    python = sys.executable or "python"

    return {
        "command": python,
        "args": ["-m", "server.mcp_stdio"],
        "cwd": abs_project,
        "env": env,
    }


def generate_config(
    platform: str,
    project_path: str = ".",
    profile: str = DEFAULT_PROFILE,
    **env_overrides: str,
) -> dict:
    """Generate MCP config JSON for a specific platform.

    Args:
        platform: One of ``claude_desktop``, ``cursor``, ``claude_code``,
                  ``vscode``, ``windsurf``, ``cline``.
        project_path: Path to the StackMemory project root.
        profile: MCP tool profile (minimal / work / admin / full). Default
                 ``work`` keeps the advertised tool surface small.
        **env_overrides: Extra environment variables for the server process.

    Returns:
        A dict ready to be serialised as JSON.
    """
    if platform not in PLATFORMS:
        available = ", ".join(sorted(PLATFORMS.keys()))
        raise ValueError(
            f"Unknown platform '{platform}'. Available: {available}"
        )

    server_entry = _build_server_entry(project_path, profile=profile, **env_overrides)

    # Claude Desktop and Claude Code use "mcpServers" top-level key
    return {
        "mcpServers": {
            "stackmemory": server_entry,
        },
    }


def generate_all_configs(
    output_dir: str = "./configs",
    project_path: str = ".",
    profile: str = DEFAULT_PROFILE,
) -> dict[str, str]:
    """Generate configs for all platforms and write to *output_dir*.

    Returns:
        ``{platform_name: file_path}`` mapping for every generated file.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    result: dict[str, str] = {}

    for platform, meta in PLATFORMS.items():
        cfg = generate_config(platform, project_path=project_path, profile=profile)

        # For platforms that live in subdirectories, create those dirs
        file_name = meta["file_name"]
        if meta["file_path"]:
            # e.g. ".cursor/mcp.json" → nested under output_dir
            rel_dir = str(Path(meta["file_path"]).parent)
            target_dir = out / rel_dir
            target_dir.mkdir(parents=True, exist_ok=True)
            target_file = target_dir / file_name
        else:
            target_file = out / file_name

        target_file.write_text(
            json.dumps(cfg, indent=2) + "\n",
            encoding="utf-8",
        )
        result[platform] = str(target_file)

    return result


# ── CLI entry point ─────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate MCP configuration files for all platforms.",
    )
    parser.add_argument(
        "--output",
        default="./configs",
        help="Output directory (default: ./configs)",
    )
    parser.add_argument(
        "--project",
        default=".",
        help="Path to StackMemory project root (default: .)",
    )
    args = parser.parse_args()

    generated = generate_all_configs(output_dir=args.output, project_path=args.project)
    for platform, path in sorted(generated.items()):
        print(f"  {platform:20s} → {path}")
    print(f"\nGenerated {len(generated)} config files.")