"""MCP client config, scaffolding, profiles and the stdio server.

A slice of the ``levh`` CLI. The parsers and the dispatch chain stay in
server/cli.py; this module holds the implementations.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from server.core.runtime_config import resolve_runtime_config, runtime_env


def cmd_mcp_config(args: argparse.Namespace) -> int:
    """Print MCP client configuration JSON to stdout."""
    from server.configs import generate_config, normalize_platform

    try:
        platform = normalize_platform(args.platform)
    except ValueError as exc:
        print(f"  {exc}", file=sys.stderr)
        return 1

    from server.tools.profiles import (
        UnknownProfileError,
        resolve_profile,
        tools_for_profile,
    )

    try:
        profile = resolve_profile(getattr(args, "profile", None))
    except UnknownProfileError as exc:
        print(f"  {exc}", file=sys.stderr)
        return 1

    runtime = resolve_runtime_config(
        explicit={
            "embedder_mode": args.embedder_mode,
            "database_path": args.db_path,
        }
    )
    cfg = generate_config(
        platform,
        project_path=".",
        profile=profile,
        **runtime_env(runtime),
    )
    print(json.dumps(cfg, indent=2))
    # Report the surface on stderr so stdout stays a clean, pipeable JSON blob.
    n = len(tools_for_profile(profile))
    print(
        f"  MCP profile '{profile}' → {n} tools advertised "
        f"(change with --profile minimal|work|admin|full)",
        file=sys.stderr,
    )
    return 0


def cmd_mcp_init(args: argparse.Namespace) -> int:
    """Scaffold a new MCP server project."""
    from server.scaffold import ScaffoldError, generate_project
    from server.tools.profiles import UnknownProfileError, resolve_profile

    try:
        profile = resolve_profile(getattr(args, "profile", None))
    except UnknownProfileError as exc:
        print(f"  {exc}", file=sys.stderr)
        return 1

    try:
        written = generate_project(
            args.name,
            target_dir=args.directory,
            with_memory=args.with_memory,
            template=args.template,
            profile=profile,
            deploy=getattr(args, "deploy", "") or "",
            force=args.force,
        )
    except ScaffoldError as exc:
        print(f"  {exc}", file=sys.stderr)
        return 1

    root = written[0].parent if len(written) == 1 else Path(args.directory).resolve() / args.name
    print(f"\n  Created {args.name} ({len(written)} files) in {root}")
    for path in written:
        print(f"    {path.relative_to(root.parent)}")

    module = args.name.replace("-", "_").replace(".", "_").lower()
    print("\n  Next:")
    print(f"    cd {args.name}")
    print("    pip install -r requirements.txt")
    print(f"    python -m {module}.server")
    if args.with_memory:
        print(f"\n  Memory tools mounted with the '{profile}' profile, sharing the")
        print("  LEVH database — stored memories show up in the dashboard too.")
    return 0


def cmd_mcp_profiles(_args: argparse.Namespace) -> int:
    """List the MCP tool profiles and how many tools each advertises."""
    from server.tools.profiles import (
        DEFAULT_PROFILE,
        profile_counts,
        tools_for_profile,
    )

    counts = profile_counts()
    print("\n  MCP tool profiles (advertise fewer tools = better tool selection)")
    print("  " + "=" * 52)
    for name, count in counts.items():
        marker = "  (default)" if name == DEFAULT_PROFILE else ""
        print(f"  {name:8s} {count:3d} tools{marker}")
    print("  " + "=" * 52)
    print("  minimal ⊂ work ⊂ admin ⊂ full")
    print(f"  Set LEVH_MCP_PROFILE or `mcp config --profile <name>`.\n")
    # Show the minimal set explicitly — it's short and clarifies the core loop.
    print("  minimal tools: " + ", ".join(sorted(tools_for_profile("minimal"))))
    return 0


def cmd_mcp_stdio(_args: argparse.Namespace) -> int:
    """Launch the MCP stdio server (wraps server.mcp_stdio)."""
    from server.mcp_stdio import mcp
    mcp.run(transport="stdio")
    return 0
