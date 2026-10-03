"""Auto Checkpoint — Timer-based periodic checkpoint creation.

Usage:
    levh checkpoint auto --interval 300          # Every 5 minutes
    levh checkpoint auto --interval 600 --project my-repo
    levh checkpoint auto --stop                  # Stop auto-checkpoint
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from datetime import datetime, timezone


logger = logging.getLogger("levh.auto_checkpoint")

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

    def _signal_handler(_sig, _frame):
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

            last_rowid = 0
            while _auto_checkpoint_running:
                # Delta-based, non-repeating summary: only memories created
                # since the last checkpoint are folded in, and the checkpoint
                # is skipped entirely when nothing new arrived. This keeps
                # consecutive auto summaries meaningful instead of a static
                # boilerplate line repeated every interval.
                last_rowid, count, created = await create_delta_checkpoint(
                    engine,
                    agent=agent,
                    session_id=None,
                    project=project,
                    since_rowid=last_rowid,
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


async def create_delta_checkpoint(
    engine,
    *,
    agent: str = "cli",
    session_id: str | None = None,
    project: str | None = None,
    since_rowid: int = 0,
) -> tuple[int, int, bool]:
    """Create one checkpoint summarizing memories inserted after ``since_rowid``.

    Returns ``(since_rowid, count, created)`` where the first element is the
    insertion watermark to feed back as the next cursor, ``count`` is how many
    new memories this checkpoint captured, and ``created`` is True when a
    checkpoint was actually written. When nothing is newer than the cursor it
    returns ``(since_rowid, 0, False)`` and writes nothing, so consecutive
    auto-checkpoints never repeat the same boilerplate.

    The cursor is a SQLite ``rowid``, not a ``created_at`` (#379). The original
    version compared wall-clock strings with ``>``: ``created_at`` comes from
    ``datetime.now()``, whose resolution is the platform's — 15.625 ms on
    Windows — so two memories written inside one tick share a timestamp, the
    strict comparison drops the newer one, and the pass reports "nothing new"
    while a memory sits unsummarized. Insertion order cannot tie.

    The summary is produced through ``summarize_texts`` — a real aggregation of
    the delta's contents, not a timestamped placeholder — with an offline
    extractive fallback so it works without any API key.
    """
    tracker = getattr(engine, "agent_tracker", None)
    if not tracker:
        return since_rowid, 0, False

    from server.core.summarizer import summarize_texts

    delta, watermark = await engine.episodic.get_after_rowid(since_rowid)
    if not delta:
        return since_rowid, 0, False

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
    return watermark, len(delta), True


async def _background_loop(
    engine,
    *,
    agent: str,
    session_id: str | None,
    project: str | None,
    interval: int,
) -> None:
    last_rowid = 0
    while True:
        try:
            last_rowid, _count, _created = await create_delta_checkpoint(
                engine,
                agent=agent,
                session_id=session_id,
                project=project,
                since_rowid=last_rowid,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("auto-checkpoint pass failed; scheduler continues")
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