"""Config generation for clients that do not speak the "mcpServers" dialect.

Codex reads TOML, Hermes reads YAML, and opencode uses a different JSON shape,
so a generator that always emitted `mcpServers` JSON produced files those three
clients silently ignore.
"""

from __future__ import annotations

import json
import tomllib

import pytest

from server.configs import (
    PLATFORMS,
    generate_config,
    normalize_platform,
    render_config,
)
from server.core.onboarding import public_client_options


REQUESTED_CLIENTS = ("claude_code", "cline", "jcode", "omp", "opencode", "codex", "hermes")


@pytest.mark.parametrize("alias", REQUESTED_CLIENTS)
def test_every_offered_client_resolves_and_renders(alias):
    platform = normalize_platform(alias)
    cfg = generate_config(platform, project_path=".")
    text = render_config(platform, cfg)
    assert text.strip(), f"{alias} rendered an empty config"
    assert text.endswith("\n")


@pytest.mark.parametrize("alias", REQUESTED_CLIENTS)
def test_offered_clients_are_listed_in_the_dashboard(alias):
    listed = {item["id"] for item in public_client_options()}
    assert alias in listed


def test_mcp_servers_dialect_is_unchanged():
    cfg = generate_config("cursor", project_path=".")
    assert set(cfg) == {"mcpServers"}
    entry = cfg["mcpServers"]["levh"]
    assert entry["command"] == "levh"
    assert entry["args"] == ["mcp", "stdio"]
    assert json.loads(render_config("cursor", cfg)) == cfg


def test_jcode_and_omp_use_the_mcp_servers_dialect():
    for platform in ("jcode", "omp"):
        cfg = generate_config(platform, project_path=".")
        assert "levh" in cfg["mcpServers"]


def test_opencode_uses_its_own_json_shape():
    cfg = generate_config("opencode", project_path=".")
    entry = cfg["mcp"]["levh"]
    assert entry["type"] == "local"
    # opencode takes one argv array, not command + args.
    assert entry["command"] == ["levh", "mcp", "stdio"]
    assert entry["enabled"] is True
    # ...and spells the environment block "environment".
    assert "env" not in entry
    assert entry["environment"]["LEVH_MCP_PROFILE"] == "work"
    assert json.loads(render_config("opencode", cfg)) == cfg


def test_codex_renders_parseable_toml():
    cfg = generate_config("codex", project_path=".", profile="full")
    parsed = tomllib.loads(render_config("codex", cfg))
    entry = parsed["mcp_servers"]["levh"]
    assert entry["command"] == "levh"
    assert entry["args"] == ["mcp", "stdio"]
    assert entry["env"]["LEVH_MCP_PROFILE"] == "full"


def test_hermes_renders_yaml_hermes_can_read():
    cfg = generate_config("hermes", project_path=".")
    text = render_config("hermes", cfg)
    assert text.startswith("mcp_servers:\n")
    assert '  levh:\n' in text
    assert '    command: "levh"' in text
    assert '    args: ["mcp", "stdio"]' in text
    assert '      LEVH_MCP_PROFILE: "work"' in text
    # Hermes documents no cwd key for stdio servers.
    assert "cwd" not in text

    yaml = pytest.importorskip("yaml")
    assert yaml.safe_load(text)["mcp_servers"]["levh"]["args"] == ["mcp", "stdio"]


def test_generate_all_configs_writes_native_file_formats(tmp_path):
    from server.configs import generate_all_configs

    written = generate_all_configs(output_dir=str(tmp_path), project_path=".")
    for platform in PLATFORMS:
        assert platform in written

    tomllib.loads((tmp_path / ".codex" / "config.toml").read_text())
    assert (tmp_path / ".hermes" / "config.yaml").read_text().startswith("mcp_servers:")
    json.loads((tmp_path / "opencode.json").read_text())
    json.loads((tmp_path / ".jcode" / "mcp.json").read_text())


def test_env_values_are_quoted_in_toml_and_yaml(tmp_path):
    """Windows-style paths must not break the hand-rolled emitters."""
    cfg = generate_config("codex", project_path=".", SQLITE_DB_PATH=r"C:\Users\a b\levh.db")
    assert tomllib.loads(render_config("codex", cfg))["mcp_servers"]["levh"]["env"][
        "SQLITE_DB_PATH"
    ] == r"C:\Users\a b\levh.db"

    cfg = generate_config("hermes", project_path=".", SQLITE_DB_PATH="path: with colon")
    yaml = pytest.importorskip("yaml")
    loaded = yaml.safe_load(render_config("hermes", cfg))
    assert loaded["mcp_servers"]["levh"]["env"]["SQLITE_DB_PATH"] == "path: with colon"
