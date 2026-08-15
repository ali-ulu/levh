"""The session-start hook: memory that arrives without being asked for.

A memory tool the assistant has to be *told* to consult is not memory, it is a
filing cabinet. This hook is what closes that gap — Claude Code runs it when a
session begins and puts its output into the conversation.

Two properties matter more than the feature itself, and both are tested here:
it must never fail the session, and it must not overwrite settings that belong
to someone else.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import pytest_asyncio

os.environ.setdefault("EMBEDDER_MODE", "hash")

from server.commands import hooks  # noqa: E402
from server.core.guard import GuardService  # noqa: E402
from server.core.memory_engine import MemoryEngine  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """An empty project directory, since the hook installs relative to cwd."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _install(client="claude-code", limit=5):
    import argparse

    return hooks.cmd_hook(
        argparse.Namespace(hook_command="install", client=client, limit=limit)
    )


def _uninstall(client="claude-code"):
    import argparse

    return hooks.cmd_hook(
        argparse.Namespace(hook_command="uninstall", client=client, limit=5)
    )


# ── Installation ─────────────────────────────────────────────────────


def test_install_writes_an_executable_script_and_registers_it(project):
    assert _install() == 0

    script = project / ".claude/hooks/levh-session-start.sh"
    assert script.is_file()
    assert os.access(script, os.X_OK), "Claude Code has to be able to run it"

    settings = json.loads((project / ".claude/settings.json").read_text())
    entries = settings["hooks"]["SessionStart"][0]["hooks"]
    assert entries[0]["type"] == "command"
    assert "levh-session-start.sh" in entries[0]["command"]


def test_the_registered_command_survives_a_project_path_with_spaces(project):
    """Claude Code expands ``$CLAUDE_PROJECT_DIR`` into a shell command line.

    Real project directories have spaces in them — "social mcp", "My Documents".
    Unquoted, the expansion word-splits and the hook silently never runs. That
    is the worst failure mode a memory tool has: the session starts blank and
    nothing tells you why it forgot.
    """
    _install()
    settings = json.loads((project / ".claude/settings.json").read_text())
    command = settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]

    # What the shell sees once Claude Code has substituted the variable. Split
    # with POSIX rules rather than by running a shell, so the test is offline
    # and behaves the same on Windows as it does in CI.
    expanded = command.replace("$CLAUDE_PROJECT_DIR", "/home/u/a project with spaces")
    words = shlex.split(expanded)

    assert words == ["/home/u/a project with spaces/.claude/hooks/levh-session-start.sh"], (
        "the expansion word-split; the shell would look for a command called "
        f"{words[0]!r} and silently find nothing"
    )


def test_installing_over_a_legacy_unquoted_entry_repairs_it(project):
    """An upgrade has to fix the broken command, not step over it.

    Every project installed before the quoting fix carries the unquoted entry.
    Recognising it as "already installed" and returning leaves those projects
    broken forever, because nothing in the output suggests uninstalling first.
    """
    settings_path = project / ".claude/settings.json"
    settings_path.parent.mkdir(parents=True)
    legacy = "$CLAUDE_PROJECT_DIR/.claude/hooks/levh-session-start.sh"
    settings_path.write_text(
        json.dumps(
            {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": legacy}]}]}}
        )
    )

    assert _install() == 0

    settings = json.loads(settings_path.read_text())
    commands = [e["command"] for g in settings["hooks"]["SessionStart"] for e in g["hooks"]]
    assert commands == ['"$CLAUDE_PROJECT_DIR/.claude/hooks/levh-session-start.sh"'], (
        "the stale entry survived the upgrade"
    )


def test_installing_twice_changes_nothing(project):
    _install()
    first = (project / ".claude/settings.json").read_text()

    assert _install() == 0
    assert (project / ".claude/settings.json").read_text() == first


def test_install_keeps_hooks_that_are_not_ours(project):
    """Someone else's settings are not ours to overwrite."""
    settings_path = project / ".claude/settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [{"hooks": [{"type": "command", "command": "./setup.sh"}]}],
                    "PreToolUse": [{"hooks": [{"type": "command", "command": "./guard.sh"}]}],
                },
                "model": "opus",
            }
        )
    )

    _install()
    settings = json.loads(settings_path.read_text())

    commands = [e["command"] for g in settings["hooks"]["SessionStart"] for e in g["hooks"]]
    assert "./setup.sh" in commands
    assert any("levh-session-start.sh" in c for c in commands)
    assert settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "./guard.sh"
    assert settings["model"] == "opus", "unrelated settings must survive"


def test_a_broken_settings_file_is_reported_not_overwritten(project):
    settings_path = project / ".claude/settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text("{ this is not json")

    assert _install() == 1
    assert settings_path.read_text() == "{ this is not json"


# ── Removal ──────────────────────────────────────────────────────────


def test_uninstall_removes_only_our_entry(project):
    settings_path = project / ".claude/settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps(
            {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "./setup.sh"}]}]}}
        )
    )
    _install()

    assert _uninstall() == 0

    assert not (project / ".claude/hooks/levh-session-start.sh").exists()
    settings = json.loads(settings_path.read_text())
    commands = [e["command"] for g in settings["hooks"]["SessionStart"] for e in g["hooks"]]
    assert commands == ["./setup.sh"]


def test_uninstalling_when_nothing_is_installed_is_not_an_error(project):
    assert _uninstall() == 0


# ── The script itself ────────────────────────────────────────────────

# Windows cannot exec a .sh directly (WinError 193), so the script is always
# handed to a shell. Claude Code runs the hook the same way, and on POSIX this
# is equivalent to executing it — the shebang asks for /bin/sh either way.
SH = shutil.which("sh")

needs_shell = pytest.mark.skipif(
    SH is None, reason="no POSIX shell available to run the hook script"
)


def _hook_argv(project: Path) -> list[str]:
    return [SH, str(project / ".claude/hooks/levh-session-start.sh")]


def _run_hook(project: Path, db: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        _hook_argv(project),
        cwd=project,
        capture_output=True,
        text=True,
        timeout=180,
        env={
            **os.environ,
            "SQLITE_DB_PATH": str(db),
            "EMBEDDER_MODE": "hash",
            "PYTHONPATH": str(REPO_ROOT),
        },
    )


@needs_shell
def test_the_hook_stays_silent_when_there_is_nothing_to_say(project):
    """An empty brief in every conversation is noise, not memory."""
    _install()

    result = _run_hook(project, project / "empty.db")

    assert result.returncode == 0
    assert result.stdout.strip() == ""


@needs_shell
def test_the_hook_never_fails_the_session(project):
    """A memory tool that stops you starting work is worse than none."""
    _install()

    result = subprocess.run(
        _hook_argv(project),
        cwd=project,
        capture_output=True,
        text=True,
        timeout=180,
        # No PYTHONPATH and a nonsense interpreter target: levh cannot run.
        env={**os.environ, "PYTHONPATH": "/nonexistent", "SQLITE_DB_PATH": "/nonexistent/x.db"},
    )

    assert result.returncode == 0, "the hook must exit 0 even when levh is broken"
    assert result.stdout.strip() == ""


@pytest.mark.asyncio
async def test_the_session_starts_knowing_the_rules_and_the_pins(project):
    """The end-to-end promise: a new session already knows what it was told."""
    db = project / "memory.db"
    engine = MemoryEngine(db_path=str(db), embedder_mode="hash")
    await engine.initialize()
    await engine.store(
        "This project uses pnpm, never npm", pinned=True, memory_type="episodic"
    )
    await GuardService(engine.db, engine).record_mistake(
        task="deploy",
        wrong_action="use git commit --no-verify",
        correct_action="run git commit with the hooks",
        severity="high",
    )
    await engine.shutdown()

    _install()
    result = _run_hook(project, db)

    assert result.returncode == 0
    assert "Rules (learned from mistakes" in result.stdout
    assert "git commit --no-verify" in result.stdout
    assert "Always Remember (pinned)" in result.stdout
    assert "pnpm" in result.stdout


# ── The git hook still works ─────────────────────────────────────────


def test_the_git_hook_is_unchanged_by_the_new_flag(project):
    """--client git is the default and must behave exactly as before."""
    subprocess.run(["git", "init", "-q", "."], cwd=project, check=True)

    import argparse

    assert (
        hooks.cmd_hook(argparse.Namespace(hook_command="install", client="git", limit=5)) == 0
    )
    hook = project / ".git/hooks/post-commit"
    assert hook.is_file()
    assert "capture" in hook.read_text()
