"""Context files, session summaries and continuity.

A slice of the ``levh`` CLI. The parsers and the dispatch chain stay in
server/cli.py; this module holds the implementations.
"""

from __future__ import annotations

from server.commands.capture import _detect_project

import argparse
import sys
from pathlib import Path



def cmd_context(args: argparse.Namespace) -> int:
    """Generate a context file from memories and print or write it."""
    import asyncio

    from server.core import engine_provider

    project = args.project or _detect_project()

    async def _run() -> str:
        engine = engine_provider.get_engine()
        await engine.initialize()
        try:
            return await engine.generate_context_file(
                project=project, style=args.style
            )
        finally:
            await engine.shutdown()

    content = asyncio.run(_run())

    if args.output:
        # The operator names the output file; writing where they asked is the
        # command's purpose, so the path is intentionally not constrained.
        Path(args.output).write_text(content, encoding="utf-8")
        print(f"  Wrote {args.output} ({len(content)} chars, project={project or 'all'})")
    else:
        print(content)
    return 0


def cmd_summarize(args: argparse.Namespace) -> int:
    """Distill a session's memories into one durable summary memory."""
    import asyncio

    from server.core import engine_provider

    async def _run() -> int:
        engine = engine_provider.get_engine()
        await engine.initialize()
        try:
            session = await engine.get_session(args.session_id)
            if not session:
                print(f"  Session not found: {args.session_id}", file=sys.stderr)
                return 1
            summary = await engine.summarize_session(args.session_id)
            if not summary:
                print(f"  Nothing to summarize — session {args.session_id} has no memories.")
                return 0
            print(f"  Summarized session '{session.name}' into memory {summary.id[:8]}...")
            print(f"\n{summary.content}")
            return 0
        finally:
            await engine.shutdown()

    return asyncio.run(_run())


def cmd_continue(args: argparse.Namespace) -> int:
    """Show context to resume work — synthesizes session DNA from recent activity."""
    import asyncio

    from server.core import engine_provider

    project = args.project or _detect_project()

    async def _run() -> str:
        engine = engine_provider.get_engine()
        await engine.initialize()
        try:
            return await engine.get_continuity_context(
                task=args.task or None,
                project=project or None,
                limit=args.limit,
                since=args.since or None,
            )
        finally:
            await engine.shutdown()

    try:
        context = asyncio.run(_run())
        if not context.strip():
            # --if-any is what the session hook uses: nothing to say, say
            # nothing, rather than pushing an empty frame into the session.
            if not getattr(args, "if_any", False):
                print("  No recent activity to resume from yet.")
            return 0
        print(context)
        return 0
    except Exception as e:
        print(f"  Error: {e}", file=sys.stderr)
        return 1
