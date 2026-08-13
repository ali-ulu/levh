"""Demo data and full exports.

A slice of the ``levh`` CLI. The parsers and the dispatch chain stay in
server/cli.py; this module holds the implementations.
"""

from __future__ import annotations

import argparse
import os
import sys



def cmd_seed_demo(args: argparse.Namespace) -> int:
    """Populate an empty store with a deterministic demo corpus so a first run
    shows a live dashboard instead of empty states."""
    import asyncio

    from server.core import engine_provider

    async def _run() -> dict:
        engine = engine_provider.get_engine()
        await engine.initialize()
        try:
            return await engine.seed_demo(force=args.force)
        finally:
            await engine.shutdown()

    result = asyncio.run(_run())
    if result.get("skipped"):
        print(
            f"  Store already has {result.get('existing', 0)} memories — nothing seeded.\n"
            "  Re-run with --force to add the demo data anyway."
        )
        return 0
    print(
        f"  Seeded {result['seeded']} demo memories -> "
        f"{result['entities']} entities ({result['entity_links']} links), "
        f"{result['trust_scored']} trust-scored, "
        f"{result['conflict_candidates']} conflict candidate(s)."
    )
    print("  Open the dashboard (`levh serve`) to explore it.")
    return 0


def cmd_export_full(args: argparse.Namespace) -> int:
    """Export memories + entity graph + trust scores + conflicts to one file."""
    import asyncio

    from server.core import engine_provider

    fmt = args.format
    # As with `levh context -o`, the operator names the destination; the export
    # is meant to land wherever they point it.
    out_path = args.out or f"levh-full-export.{fmt}"

    async def _run():
        engine = engine_provider.get_engine()
        await engine.initialize()
        try:
            from server.core.full_export import (
                PdfUnavailableError,
                build_full_export,
                export_full_sqlite,
                render_full_export_pdf,
            )

            if fmt == "json":
                import json

                export = await build_full_export(engine)
                with open(out_path, "w") as f:
                    json.dump(export, f, indent=2, default=str)
                return export["counts"]
            elif fmt == "sqlite":
                blob = await export_full_sqlite(engine)
                with open(out_path, "wb") as f:
                    f.write(blob)
                return None
            else:
                export = await build_full_export(engine)
                try:
                    blob = render_full_export_pdf(export)
                except PdfUnavailableError as exc:
                    print(f"  {exc}", file=sys.stderr)
                    return None
                with open(out_path, "wb") as f:
                    f.write(blob)
                return export["counts"]
        finally:
            await engine.shutdown()

    counts = asyncio.run(_run())
    if counts is None and fmt == "pdf":
        return 1
    print(f"  Wrote {out_path}" + (f" — {counts}" if counts else ""))
    return 0


def cmd_remove_demo(args: argparse.Namespace) -> int:
    """Remove all demo-tagged memories, leaving real data untouched."""
    import asyncio

    from server.core import engine_provider

    async def _run() -> dict:
        engine = engine_provider.get_engine()
        await engine.initialize()
        try:
            return await engine.remove_demo_data()
        finally:
            await engine.shutdown()

    result = asyncio.run(_run())
    removed = result.get("removed", 0)
    if removed == 0:
        print("  No demo data found — nothing to remove.")
    else:
        print(f"  Removed {removed} demo memories.")
    return 0


def cmd_dogfood_status(args: argparse.Namespace) -> int:
    """Show aggregate stats from the local dogfood journal."""
    import json

    from server.core.dogfood import DogfoodJournal, resolve_journal_path

    path = resolve_journal_path(
        explicit_path=args.journal or None,
        db_path=os.getenv("SQLITE_DB_PATH"),
    )
    status = DogfoodJournal(path).status()
    print(json.dumps(status, indent=2, ensure_ascii=False))
    if status["total_events"] == 0:
        print("\n  Journal is empty — dogfood collection is local-only and "
              "opt-in; nothing is recorded (or sent anywhere) by default.")
    return 0


def cmd_dogfood_export(args: argparse.Namespace) -> int:
    """Explicit user action: write the aggregate dogfood report to a file."""
    from server.core.dogfood import DogfoodJournal, resolve_journal_path

    path = resolve_journal_path(
        explicit_path=args.journal or None,
        db_path=os.getenv("SQLITE_DB_PATH"),
    )
    report = DogfoodJournal(path).export(args.output)
    print(f"Aggregate dogfood report ({report['total_events']} events) → {args.output}")
    print("Raw event lines stay in the local journal; only aggregates were exported.")
    return 0
