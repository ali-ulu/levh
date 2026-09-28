"""Store maintenance commands.

A slice of the ``levh`` CLI. The parsers and the dispatch chain stay in
server/cli.py; this module holds the implementations.

The reporting is a separate async function so it can be exercised in-process
against a live engine — the CLI wrapper only owns process concerns (building
the engine, running the loop, shutting down), not the output.
"""

from __future__ import annotations

import argparse
from typing import Callable, Optional


def cmd_reembed(args: argparse.Namespace) -> int:
    """Re-derive stored vectors from content under the active embedder."""
    import asyncio

    from server.core import engine_provider

    async def _run() -> int:
        engine = engine_provider.get_engine()
        await engine.initialize()
        try:
            return await reembed_engine(engine, project=args.project, dry_run=args.dry_run)
        finally:
            await engine.shutdown()

    return asyncio.run(_run())


async def reembed_engine(
    engine,
    project: Optional[str] = None,
    dry_run: bool = False,
    print_fn: Callable[[str], None] = print,
) -> int:
    """Run the re-embed on an initialized engine and print the summary."""
    last_printed = 0

    def _progress(done: int, total: int) -> None:
        nonlocal last_printed
        if done == total or done - last_printed >= 25:
            last_printed = done
            print_fn(f"  re-embedded {done}/{total}")

    summary = await engine.reembed_memories(
        project=project, dry_run=dry_run, on_progress=_progress
    )
    verb = "would re-embed" if summary["dry_run"] else "re-embedded"
    print_fn(
        f"\n  Embedder: {summary['provider']}/{summary['model']} "
        f"({summary['dimension']}d)"
    )
    print_fn(f"  Scanned: {summary['scanned']} memories")
    if summary["stale"]:
        breakdown = ", ".join(
            f"{name}: {count}" for name, count in sorted(summary["by_project"].items())
        )
        print_fn(f"  {verb} {summary['stale']} of them ({breakdown})")
        if summary["dry_run"]:
            print_fn("  Dry run: nothing was changed. Re-run without --dry-run.")
    else:
        print_fn("  Every stored vector already matches the active embedder.")
    return 0

