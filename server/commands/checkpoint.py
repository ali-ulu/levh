"""Checkpoint command — save work state during agent sessions.

Usage:
    levh checkpoint --agent claude-code --title "Fixed auth bug"
    levh checkpoint --agent cursor --title "Refactored API layer" --type manual
    levh checkpoint list --agent cursor
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys


def cmd_checkpoint(args: argparse.Namespace) -> int:
    """Create or list checkpoints."""
    from server.core import engine_provider

    if getattr(args, "checkpoint_command", None) == "list":
        return _cmd_list_checkpoints(args)

    async def _run() -> int:
        engine = engine_provider.get_engine()
        await engine.initialize()
        try:
            tracker = engine.agent_tracker
            if not tracker:
                print("Agent tracker not initialized", file=sys.stderr)
                return 1

            agent = getattr(args, "agent", "cli") or "cli"
            title = getattr(args, "title", "") or "Manual checkpoint"
            checkpoint_type = getattr(args, "type", "manual") or "manual"
            project = getattr(args, "project", "") or None

            # Collect recent memory IDs as context
            recent = await engine.episodic.search(limit=10)
            memory_ids = [m.id for m in recent]

            result = await tracker.create_checkpoint(
                agent_name=agent,
                title=title,
                summary=f"Checkpoint created by {agent}",
                session_id=None,
                project=project,
                checkpoint_type=checkpoint_type,
                memory_ids=memory_ids,
            )

            print(f"Checkpoint created: {result['checkpoint_id'][:8]}...")
            print(f"  Agent: {result['agent_name']}")
            print(f"  Title: {result['title']}")
            print(f"  Time: {result['created_at']}")
            return 0
        finally:
            await engine.shutdown()

    return asyncio.run(_run())


def _cmd_list_checkpoints(args: argparse.Namespace) -> int:
    """List recent checkpoints."""
    from server.core import engine_provider

    async def _run() -> int:
        engine = engine_provider.get_engine()
        await engine.initialize()
        try:
            tracker = engine.agent_tracker
            if not tracker:
                print("Agent tracker not initialized", file=sys.stderr)
                return 1

            agent = getattr(args, "agent", None) or None
            project = getattr(args, "project", None) or None
            limit = getattr(args, "limit", 20) or 20

            checkpoints = await tracker.list_checkpoints(
                agent_name=agent, project=project, limit=limit
            )

            if not checkpoints:
                print("No checkpoints found.")
                return 0

            print(f"{len(checkpoints)} checkpoints:\n")
            for cp in checkpoints:
                print(f"  [{cp['checkpoint_type']}] {cp['title']}")
                print(f"    Agent: {cp['agent_name']} | {cp['created_at'][:16]}")
                if cp.get("project"):
                    print(f"    Project: {cp['project']}")
                print()

            return 0
        finally:
            await engine.shutdown()

    return asyncio.run(_run())
