"""The opt-in `levh brief` shell hook.

Installing it must only touch existing profile files, stay idempotent (a second
install is a no-op), and uninstall must remove exactly what it added without
corrupting the rest of the profile.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.commands.universal_hooks import install_shell_hook, uninstall_shell_hook


@pytest.fixture()
def fake_home(tmp_path, monkeypatch):
    """Redirect Path.home() to a temp dir so we never touch the real profile."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # Create the bash profile the installer looks for.
    (tmp_path / ".bashrc").write_text("# existing bash config\n", encoding="utf-8")
    return tmp_path


def test_install_adds_a_levh_brief_function(fake_home):
    result = install_shell_hook(limit=5)
    assert result["ok"] is True
    assert "bash" in result["updated"]

    bashrc = (fake_home / ".bashrc").read_text(encoding="utf-8")
    assert "LEVH auto-brief helper" in bashrc
    assert "levh brief()" in bashrc
    # Existing content is preserved.
    assert bashrc.startswith("# existing bash config")


def test_install_is_idempotent(fake_home):
    install_shell_hook(limit=5)
    first = (fake_home / ".bashrc").read_text(encoding="utf-8")
    result = install_shell_hook(limit=5)
    second = (fake_home / ".bashrc").read_text(encoding="utf-8")
    # Second run marks nothing new as updated and writes no duplicate block.
    assert result["updated"] == []
    assert second == first


def test_uninstall_removes_only_the_levh_block(fake_home):
    install_shell_hook(limit=5)
    result = uninstall_shell_hook()
    assert result["ok"] is True

    bashrc = (fake_home / ".bashrc").read_text(encoding="utf-8")
    assert "LEVH auto-brief helper" not in bashrc
    assert bashrc.strip() == "# existing bash config"


def test_hook_cli_accepts_the_shell_client():
    """`levh hook install --client shell` must be a valid CLI invocation."""
    from server.cli_parsers import build_parser

    parser, _args = build_parser("levh")
    namespace = parser.parse_args(["hook", "install", "--client", "shell"])
    assert namespace.hook_command == "install"
    assert namespace.client == "shell"