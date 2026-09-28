"""Store maintenance commands.

A slice of the ``levh`` CLI. The parsers and the dispatch chain stay in
server/cli.py; this module holds the implementations.
"""

from __future__ import annotations

import argparse


def cmd_reembed(args: argparse.Namespace) -> int:
    """Re-derive stored vectors from content under the active embedder."""
    import asyncio

    from server.core import engine_provider

    async def _run() -> int:
        engine = engine_provider.get_engine()
        await engine.initialize()
        try:
            last_printed = 0

            def _progress(done: int, total: int) -> None:
                nonlocal last_printed
                if done == total or done - last_printed >= 25:
                    last_printed = done
                    print(f"  re-embedded {done}/{total}", flush=True)

            summary = await engine.reembed_memories(
                project=args.project, dry_run=args.dry_run, on_progress=_progress
            )
            verb = "would re-embed" if summary["dry_run"] else "re-embedded"
            print(
                f"\n  Embedder: {summary['provider']}/{summary['model']} "
                f"({summary['dimension']}d)"
            )
            print(f"  Scanned: {summary['scanned']} memories")
            if summary["stale"]:
                breakdown = ", ".join(
                    f"{name}: {count}"
                    for name, count in sorted(summary["by_project"].items())
                )
                print(f"  {verb} {summary['stale']} of them ({breakdown})")
                if summary["dry_run"]:
                    print("  Dry run: nothing was changed. Re-run without --dry-run.")
            else:
                print("  Every stored vector already matches the active embedder.")
            return 0
        finally:
            await engine.shutdown()

    return asyncio.run(_run())
