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

    interval = getattr(args, "interval", 600) or 600
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

            last_created: str | None = None
            while _auto_checkpoint_running:
                # Delta-based, non-repeating summary: only memories created
                # since the last checkpoint are folded in, and the checkpoint
                # is skipped entirely when nothing new arrived. This keeps
                # consecutive auto summaries meaningful instead of a static
                # boilerplate line repeated every interval.
                last_created, count, created = await create_delta_checkpoint(
                    engine,
                    agent=agent,
                    session_id=None,
                    project=project,
                    last_created=last_created,
                )

                ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
                if created:
                    print(f"  [{ts}] Auto-checkpoint: {count} new memories summarized")
                else:
                    print(f"  [{ts}] No new memories since last checkpoint; skipped")

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

# ── Reusable delta summarization ──────────────────────────────────────
# Shared by the `levh checkpoint auto` foreground loop and the background
# task the MCP stdio server starts when LEVH_AUTO_CHECKPOINT is enabled.
# Keeping one code path means the summary logic is tested once, offline.

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _as_utc_dt(value: str | None) -> datetime:
    """Parse a stored created_at into a tz-aware datetime (UTC default)."""
    if not value:
        return _EPOCH
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return _EPOCH


def _mem_dt(memory) -> datetime:
    return _as_utc_dt(getattr(memory, "created_at", None))


async def create_delta_checkpoint(
    engine,
    *,
    agent: str = "cli",
    session_id: str | None = None,
    project: str | None = None,
    last_created: str | None = None,
) -> tuple[str | None, int, bool]:
    """Create one checkpoint summarizing memories newer than ``last_created``.

    Returns ``(last_created, count, created)`` where the first element is the
    newest ``created_at`` seen (feed it back as the next cutoff), ``count`` is
    how many new memories this checkpoint captured, and ``created`` is True
    when a checkpoint was actually written. When nothing is newer than the
    cutoff it returns ``(last_created, 0, False)`` and writes nothing, so
    consecutive auto-checkpoints never repeat the same boilerplate.

    The summary is produced through ``summarize_texts`` — a real aggregation of
    the delta's contents, not a timestamped placeholder — with an offline
    extractive fallback so it works without any API key.
    """
    tracker = getattr(engine, "agent_tracker", None)
    if not tracker:
        return last_created, 0, False

    from server.core.summarizer import summarize_texts

    cutoff = _as_utc_dt(last_created)
    all_memories = await engine.episodic.get_all(limit=10000)
    delta = sorted(
        (m for m in all_memories if _mem_dt(m) > cutoff),
        key=_mem_dt,
    )
    if not delta:
        return last_created, 0, False

    newest = delta[-1]
    memory_ids = [m.id for m in delta]
    texts = [m.content for m in delta if m.content]

    client = getattr(getattr(engine, "_embedder", None), "_http", None)
    summary_text = await summarize_texts(texts, mode="auto", client=client) if texts else ""

    title = f"Auto summary ({datetime.now(timezone.utc).strftime('%H:%M:%S')}, {len(delta)} new)"
    await tracker.create_checkpoint(
        agent_name=agent,
        title=title,
        summary=summary_text,
        session_id=session_id,
        project=project,
        checkpoint_type="auto",
        memory_ids=memory_ids,
    )
    return newest.created_at, len(delta), True


async def _background_loop(
    engine,
    *,
    agent: str,
    session_id: str | None,
    project: str | None,
    interval: int,
) -> None:
    last_created: str | None = None
    while True:
        try:
            last_created, _count, _created = await create_delta_checkpoint(
                engine,
                agent=agent,
                session_id=session_id,
                project=project,
                last_created=last_created,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            # A summarization failure must never kill the background scheduler.
            pass
        await asyncio.sleep(interval)


def start_background_auto_checkpoint(
    *,
    engine,
    agent: str = "cli",
    session_id: str | None = None,
    project: str | None = None,
    interval: int = 600,
) -> asyncio.Task | None:
    """Start the periodic auto-checkpoint background task (MCP server side).

    Returns the created :class:`asyncio.Task`, or None when the engine has no
    agent tracker. The caller owns the task and must cancel it on shutdown.
    """
    if not getattr(engine, "agent_tracker", None):
        return None
    loop = asyncio.get_running_loop()
    return loop.create_task(
        _background_loop(
            engine,
            agent=agent,
            session_id=session_id,
            project=project,
            interval=interval,
        )
    )