"""Auto Checkpoint — Timer-based periodic checkpoint creation.

Usage:
    levh checkpoint auto --interval 300          # Every 5 minutes
    levh checkpoint auto --interval 600 --project my-repo
    levh checkpoint auto --stop                  # Stop auto-checkpoint
"""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
import time
from datetime import datetime, timezone


_auto_checkpoint_running = False


def cmd_auto_checkpoint(args: argparse.Namespace) -> int:
    """Run auto-checkpoint with a timer."""
    global _auto_checkpoint_running

    interval = getattr(args, "interval", 300) or 300
    project = getattr(args, "project", "") or None
    agent = getattr(args, "agent", "cli") or "cli"

    if getattr(args, "stop", False):
        # Send stop signal
        _stop_auto_checkpoint()
        return 0

    print(f"Starting auto-checkpoint every {interval}s for agent '{agent}'")
    if project:
        print(f"  Project: {project}")
    print("  Press Ctrl+C to stop")

    _auto_checkpoint_running = True

    def _signal_handler(sig, frame):
        global _auto_checkpoint_running
        _auto_checkpoint_running = False
        print("\nStopping auto-checkpoint...")

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    async def _run():
        from server.core import engine_provider

        engine = engine_provider.get_engine()
        await engine.initialize()
        try:
            tracker = engine.agent_tracker
            if not tracker:
                print("Error: Agent tracker not available", file=sys.stderr)
                return 1

            while _auto_checkpoint_running:
                # Create checkpoint
                recent = await engine.episodic.search(limit=10)
                memory_ids = [m.id for m in recent]

                result = await tracker.create_checkpoint(
                    agent_name=agent,
                    title=f"Auto checkpoint ({datetime.now(timezone.utc).strftime('%H:%M:%S')})",
                    summary=f"Periodic auto-checkpoint (interval: {interval}s)",
                    session_id=None,
                    project=project,
                    checkpoint_type="auto",
                    memory_ids=memory_ids,
                )

                ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
                print(f"  [{ts}] Checkpoint: {result['checkpoint_id'][:8]}...")

                # Wait for next interval
                for _ in range(interval):
                    if not _auto_checkpoint_running:
                        break
                    await asyncio.sleep(1)

            print("Auto-checkpoint stopped.")
            return 0
        finally:
            await engine.shutdown()

    return asyncio.run(_run())


def _stop_auto_checkpoint() -> None:
    """Stop the auto-checkpoint process."""
    global _auto_checkpoint_running
    _auto_checkpoint_running = False
    print("Auto-checkpoint stop signal sent.")
