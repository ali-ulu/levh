"""The git auto-capture hook.

A slice of the ``levh`` CLI. The parsers and the dispatch chain stay in
server/cli.py; this module holds the implementations.
"""

from __future__ import annotations

from server.commands.paths import _HOOK_MARKER, _HOOK_TEMPLATE

import argparse
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


def cmd_hook(args: argparse.Namespace) -> int:
    """Install/uninstall the git post-commit auto-capture hook."""
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
