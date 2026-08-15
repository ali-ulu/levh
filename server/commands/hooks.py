"""The git auto-capture hook.

A slice of the ``levh`` CLI. The parsers and the dispatch chain stay in
server/cli.py; this module holds the implementations.
"""

from __future__ import annotations

from server.commands.paths import _HOOK_MARKER, _HOOK_TEMPLATE, _SESSION_HOOK_TEMPLATE

import argparse
import json
import os
import sys
from pathlib import Path



def _hooks_dir() -> Path | None:
    import subprocess
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip()) / "hooks"
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


# ── Claude Code session start ────────────────────────────────────────

SESSION_HOOK_PATH = Path(".claude/hooks/levh-session-start.sh")
SETTINGS_PATH = Path(".claude/settings.json")


def _hook_entry() -> dict:
    # Quoted, because Claude Code expands this into a shell command line and
    # project directories have spaces in them ("social mcp", "My Documents").
    # Unquoted the expansion word-splits, the hook never runs, and the session
    # starts with no memory and no error to explain why.
    return {
        "type": "command",
        "command": '"$CLAUDE_PROJECT_DIR/' + SESSION_HOOK_PATH.as_posix() + '"',
    }


def _is_levh_entry(entry: dict) -> bool:
    return SESSION_HOOK_PATH.name in str(entry.get("command", ""))


def _read_settings() -> dict:
    if not SETTINGS_PATH.exists():
        return {}
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise ValueError(f"{SETTINGS_PATH} is not valid JSON; fix or move it first")


def _write_settings(settings: dict) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")


def _resolved_db_path() -> str:
    """The database this machine actually uses, as an absolute path.

    Mirrors ``server.core.db.schema``'s default. Resolving it here — at install
    time, in the shell the user ran the command from — is the point: the hook
    itself runs with an unpredictable working directory, so a relative default
    would silently point at an empty per-directory database.
    """
    return os.path.abspath(os.getenv("SQLITE_DB_PATH", "./stackmemory.db"))


def _install_session_hook(limit: int) -> int:
    """Register a SessionStart hook that injects the continuity brief."""
    SESSION_HOOK_PATH.parent.mkdir(parents=True, exist_ok=True)
    SESSION_HOOK_PATH.write_text(
        _SESSION_HOOK_TEMPLATE.format(
            marker=_HOOK_MARKER,
            python=sys.executable,
            limit=limit,
            db_path=_resolved_db_path(),
        ),
        encoding="utf-8",
    )
    SESSION_HOOK_PATH.chmod(0o755)

    try:
        settings = _read_settings()
    except ValueError as exc:
        print(f"  {exc}", file=sys.stderr)
        return 1

    # Merge rather than replace: the file may already carry hooks that have
    # nothing to do with LEVH, and overwriting someone's settings to install a
    # memory tool would be its own kind of forgetting.
    hooks = settings.setdefault("hooks", {})
    groups = hooks.setdefault("SessionStart", [])

    # An entry of ours that isn't the current one is a stale install, not a
    # finished one. Rewriting it is the only way a project that predates a fix
    # to the command line ever receives that fix — treating it as "already
    # installed" would leave it broken with nothing to suggest reinstalling.
    canonical = _hook_entry()
    found = upgraded = False
    for group in groups:
        entries = group.get("hooks", [])
        for index, entry in enumerate(entries):
            if not _is_levh_entry(entry):
                continue
            found = True
            if entry != canonical:
                entries[index] = canonical
                upgraded = True

    if found:
        _write_settings(settings)
        if upgraded:
            print(f"  Updated the existing entry in {SETTINGS_PATH}")
            return 0
        print(f"  Already installed: {SESSION_HOOK_PATH}")
        return 0

    groups.append({"hooks": [canonical]})
    _write_settings(settings)

    print(f"  Installed session hook: {SESSION_HOOK_PATH}")
    print(f"  Registered in {SETTINGS_PATH}")
    print()
    print("  Every new Claude Code session in this project now starts with your")
    print("  continuity brief already in context — no need to ask for it.")
    return 0


def _uninstall_session_hook() -> int:
    removed = False
    if SESSION_HOOK_PATH.exists():
        SESSION_HOOK_PATH.unlink()
        print(f"  Removed {SESSION_HOOK_PATH}")
        removed = True

    try:
        settings = _read_settings()
    except ValueError as exc:
        print(f"  {exc}", file=sys.stderr)
        return 1

    groups = settings.get("hooks", {}).get("SessionStart", [])
    kept = []
    for group in groups:
        entries = [e for e in group.get("hooks", []) if not _is_levh_entry(e)]
        if len(entries) != len(group.get("hooks", [])):
            removed = True
        if entries:
            kept.append({**group, "hooks": entries})
    if groups:
        if kept:
            settings["hooks"]["SessionStart"] = kept
        else:
            settings["hooks"].pop("SessionStart", None)
            if not settings["hooks"]:
                settings.pop("hooks", None)
        _write_settings(settings)

    print("  Session hook removed." if removed else "  No LEVH session hook installed.")
    return 0


def cmd_hook(args: argparse.Namespace) -> int:
    """Install/uninstall an auto-capture or session-start hook."""
    if getattr(args, "client", "git") == "claude-code":
        if args.hook_command == "uninstall":
            return _uninstall_session_hook()
        return _install_session_hook(getattr(args, "limit", 5))

    hooks = _hooks_dir()
    if hooks is None:
        print("  Not inside a git repository.", file=sys.stderr)
        return 1
    hook_file = hooks / "post-commit"

    if args.hook_command == "uninstall":
        if hook_file.exists() and _HOOK_MARKER in hook_file.read_text(encoding="utf-8"):
            hook_file.unlink()
            print(f"  Removed {hook_file}")
            return 0
        print("  No LEVH hook installed.")
        return 0

    # install
    if hook_file.exists():
        existing = hook_file.read_text(encoding="utf-8")
        if _HOOK_MARKER in existing:
            print(f"  Hook already installed: {hook_file}")
            return 0
        print(f"  A post-commit hook already exists: {hook_file}", file=sys.stderr)
        print("  Append the LEVH capture line manually, or remove it first.", file=sys.stderr)
        return 1

    hooks.mkdir(parents=True, exist_ok=True)
    hook_file.write_text(
        _HOOK_TEMPLATE.format(marker=_HOOK_MARKER, python=sys.executable),
        encoding="utf-8",
    )
    hook_file.chmod(0o755)
    print(f"  Installed post-commit hook: {hook_file}")
    print("  Every commit message will now be captured as a memory (source=git-hook).")
    return 0
