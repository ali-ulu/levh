"""The Cursor MCP/cursorrules installer (#97).

Checks that `limit` is actually honoured in the continuity brief written into
.cursorrules, and that the non-cursor installers do not accept a dead `limit`
parameter anymore.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.commands.universal_hooks import (
    install_claude_code_hook,
    install_claude_desktop_hook,
    install_cursor_hook,
    install_universal_hook,
    install_vscode_hook,
    install_windsurf_hook,
)


@pytest.fixture()
def fake_cwd(tmp_path, monkeypatch):
    """Run the installer inside a temp dir so we never touch the repo."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_cursor_hook_honours_limit_in_cursorrules(fake_cwd):
    result = install_cursor_hook(limit=7)
    assert result["ok"] is True
    rules = (fake_cwd / ".cursorrules").read_text(encoding="utf-8")
    assert "--limit 7 --if-any" in rules


def test_default_limit_is_5(fake_cwd):
    install_cursor_hook()
    rules = (fake_cwd / ".cursorrules").read_text(encoding="utf-8")
    assert "--limit 5 --if-any" in rules


@pytest.mark.parametrize(
    "installer",
    [install_vscode_hook, install_windsurf_hook, install_claude_desktop_hook],
)
def test_non_cursor_installers_take_no_limit(fake_cwd, installer):
    result = installer()
    assert result["ok"] is True
    assert "config_path" in result


@pytest.mark.parametrize(
    "installer, config_rel",
    [
        (install_cursor_hook, ".cursor/mcp.json"),
        (install_vscode_hook, ".vscode/mcp.json"),
        (install_windsurf_hook, ".windsurf/mcp.json"),
        (install_claude_desktop_hook, "claude_desktop_config_levh.json"),
    ],
)
def test_install_preserves_other_mcp_servers(fake_cwd, installer, config_rel):
    config_path = fake_cwd / config_rel
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        '{"mcpServers": {"existing": {"command": "keep"}}}\n', encoding="utf-8"
    )

    installer()

    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert "existing" in config["mcpServers"]
    assert config["mcpServers"]["levh"]["command"] == "levh"


def test_uninstall_removes_only_levh_entry(fake_cwd):
    from server.commands.universal_hooks import uninstall_universal_hook

    config_path = fake_cwd / ".cursor/mcp.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "existing": {"command": "keep"},
                    "levh": {"command": "levh"},
                }
            }
        ),
        encoding="utf-8",
    )

    result = uninstall_universal_hook("cursor")
    assert result["cursor"]["ok"] is True

    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert "levh" not in config["mcpServers"]
    assert "existing" in config["mcpServers"]


def test_install_is_idempotent(fake_cwd):
    install_cursor_hook()
    first = (fake_cwd / ".cursor/mcp.json").read_text(encoding="utf-8")
    install_cursor_hook()
    second = (fake_cwd / ".cursor/mcp.json").read_text(encoding="utf-8")
    assert second == first


# ── The checkpoint half-feature that never ran (#124) ────────────────


def test_no_installer_advertises_a_checkpoint_it_never_installs(fake_cwd, monkeypatch):
    """``--with-checkpoint`` was accepted, forwarded, and then dropped.

    Every entry point took the flag and none of them read it: the Claude Code
    installer swallowed it, the CLI never defined the argument so the command
    was unreachable, and the template it was meant to write had no callers.
    A parameter nothing reads is a promise the code cannot keep, so the
    installers dropped it rather than keeping the third of the three possible
    states (flag present, behaviour absent) the issue called the worst.
    """
    import inspect

    from server.commands import hooks as hook_commands

    for fn in (
        install_claude_code_hook,
        install_universal_hook,
    ):
        assert "with_checkpoint" not in inspect.signature(fn).parameters

    # The CLI has to keep rejecting the flag, rather than accepting it and
    # silently doing nothing.
    from server.cli_parsers import build_parser

    parser, _ = build_parser("levh")
    with pytest.raises(SystemExit):
        parser.parse_args(["hook", "install", "--client", "claude-code", "--with-checkpoint"])

    # And nothing may call the install path with a checkpoint keyword either.
    calls = []
    real = hook_commands._install_session_hook

    def spy(limit):
        calls.append(limit)
        return real(limit)

    monkeypatch.setattr(hook_commands, "_install_session_hook", spy)
    result = install_universal_hook("claude-code", limit=3)
    assert result["claude-code"]["ok"] is True
    assert calls == [3]


def test_the_unused_checkpoint_template_is_gone():
    """The template had no callers anywhere in the repo; leaving it behind is
    what made the feature look half-built and the comment on it untrue."""
    import server.commands.universal_hooks as universal_hooks

    assert not hasattr(universal_hooks, "_CHECKPOINT_TEMPLATE")

    source = Path(universal_hooks.__file__).read_text(encoding="utf-8")
    assert "--with-checkpoint" not in source

    repo_root = Path(__file__).resolve().parent.parent
    hits = [
        path
        for path in list((repo_root / "docs").rglob("*.md"))
        + list((repo_root / "server").rglob("*.py"))
        + [repo_root / "README.md"]
        if path.is_file() and "--with-checkpoint" in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert not hits, f"the flag is still advertised in: {sorted(str(p) for p in hits)}"