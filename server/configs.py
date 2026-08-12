"""Multi-Platform MCP Config Generator.

Generates MCP configuration JSON for each AI-coding platform.
All platforms use stdio transport with ``python -m server.mcp_stdio``.

Usage (CLI):
    python -m server.configs --output ./configs --project /path/to/levh

Usage (programmatic):
    from server.configs import generate_config, generate_all_configs
    cfg = generate_config("claude_desktop", project_path="/path/to/levh")
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
    "jcode": {
        "file_name": "mcp.json",
        "file_path": ".jcode/mcp.json",
        "description": "jcode (CLI)",
    },
    "omp": {
        "file_name": "mcp.json",
        "file_path": ".omp/mcp.json",
        "description": "oh-my-pi / omp (CLI)",
    },
    # The three below do not speak the "mcpServers" JSON dialect; see FORMATS.
    "opencode": {
        "file_name": "opencode.json",
        "file_path": "opencode.json",
        "description": "opencode (CLI)",
        "format": "opencode_json",
    },
    "codex": {
        "file_name": "config.toml",
        "file_path": ".codex/config.toml",
        "description": "Codex CLI (OpenAI)",
        "format": "codex_toml",
    },
    "hermes": {
        "file_name": "config.yaml",
        "file_path": ".hermes/config.yaml",
        "description": "Hermes Agent (Nous Research)",
        "format": "hermes_yaml",
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
    "jcode": "jcode",
    "omp": "omp",
    "oh_my_pi": "omp",
    "opencode": "opencode",
    "codex": "codex",
    "hermes": "hermes",
    "generic": "claude_desktop",
}

# Serialisation dialect per platform. Anything not listed here uses the
# "mcpServers" JSON object that most clients adopted from Claude Desktop.
DEFAULT_FORMAT = "mcp_servers_json"


def platform_format(platform: str) -> str:
    return PLATFORMS[platform].get("format", DEFAULT_FORMAT)


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

    ``profile`` sets LEVH_MCP_PROFILE so the client advertises a focused
    tool surface (default ``work``) instead of all 61 tools — better
    tool-selection accuracy. Pass ``full`` to opt back into everything.
    """
    abs_project = os.path.abspath(project_path)
    runtime = resolve_runtime_config(cwd=abs_project)
    env: dict[str, str] = {
        **runtime_env(runtime),
        "LEVH_MCP_PROFILE": resolve_profile(profile),
    }
    env.update({k: str(v) for k, v in env_overrides.items()})

    return {
        "command": "levh",
        "args": ["mcp", "stdio"],
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
        project_path: Path to the LEVH project root.
        profile: MCP tool profile (minimal / work / admin / full). Default
                 ``work`` keeps the advertised tool surface small.
        **env_overrides: Extra environment variables for the server process.

    Returns:
        The platform's native config structure, ready to be serialised by
        :func:`render_config` into that platform's file format.
    """
    if platform not in PLATFORMS:
        available = ", ".join(sorted(PLATFORMS.keys()))
        raise ValueError(
            f"Unknown platform '{platform}'. Available: {available}"
        )

    server_entry = _build_server_entry(project_path, profile=profile, **env_overrides)
    fmt = platform_format(platform)

    if fmt == "opencode_json":
        # opencode keys the block "mcp", tags each entry local/remote, takes the
        # command as one argv array, and spells the environment "environment".
        return {
            "mcp": {
                "levh": {
                    "type": "local",
                    "command": [server_entry["command"], *server_entry["args"]],
                    "enabled": True,
                    "environment": server_entry["env"],
                }
            }
        }

    if fmt in ("codex_toml", "hermes_yaml"):
        # Both spell the block "mcp_servers". Hermes documents no cwd key, and
        # Codex reads config.toml from a fixed location, so neither can rely on
        # a working directory — the env carries absolute paths already.
        entry = {
            "command": server_entry["command"],
            "args": server_entry["args"],
            "env": server_entry["env"],
        }
        return {"mcp_servers": {"levh": entry}}

    # Claude Desktop and the clients that copied it use "mcpServers".
    return {
        "mcpServers": {
            "levh": server_entry,
        },
    }


# ── Serialisation ───────────────────────────────────────────────────


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    return json.dumps(str(value))


def _to_toml(config: dict) -> str:
    """Emit the narrow TOML shape Codex reads — tables of string scalars.

    Hand-rolled on purpose: the standard library ships a TOML reader but no
    writer, and pulling a dependency in for six lines of output is not worth it.
    """
    lines: list[str] = []
    for server_name, entry in config["mcp_servers"].items():
        lines.append(f"[mcp_servers.{server_name}]")
        for key, value in entry.items():
            if isinstance(value, dict):
                continue
            lines.append(f"{key} = {_toml_value(value)}")
        env = entry.get("env") or {}
        if env:
            lines.append("")
            lines.append(f"[mcp_servers.{server_name}.env]")
            for key, value in env.items():
                lines.append(f"{key} = {_toml_value(value)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value))  # double-quoted: valid YAML, escapes safely


def _to_yaml(config: dict) -> str:
    """Emit the narrow YAML shape Hermes reads. Hand-rolled — see _to_toml."""
    lines = ["mcp_servers:"]
    for server_name, entry in config["mcp_servers"].items():
        lines.append(f"  {server_name}:")
        for key, value in entry.items():
            if isinstance(value, dict):
                if not value:
                    continue
                lines.append(f"    {key}:")
                for k, v in value.items():
                    lines.append(f"      {k}: {_yaml_scalar(v)}")
            elif isinstance(value, list):
                rendered = ", ".join(_yaml_scalar(v) for v in value)
                lines.append(f"    {key}: [{rendered}]")
            else:
                lines.append(f"    {key}: {_yaml_scalar(value)}")
    return "\n".join(lines) + "\n"


def render_config(platform: str, config: dict) -> str:
    """Serialise *config* into the file text *platform* expects."""
    fmt = platform_format(platform)
    if fmt == "codex_toml":
        return _to_toml(config)
    if fmt == "hermes_yaml":
        return _to_yaml(config)
    return json.dumps(config, indent=2) + "\n"


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
            render_config(platform, cfg),
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
        help="Path to LEVH project root (default: .)",
    )
    args = parser.parse_args()

    generated = generate_all_configs(output_dir=args.output, project_path=args.project)
    for platform, path in sorted(generated.items()):
        print(f"  {platform:20s} → {path}")
    print(f"\nGenerated {len(generated)} config files.")
