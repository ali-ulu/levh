"""The six silent swallows removed in #220 must log *and* keep behaving.

`tests/test_error_handling_policy.py` proves the tree contains no `pass`-only
broad handler; this file proves the replacement `logger.exception(...)` calls
are actually reached on the failure path — a log line that never executes is
no better than the `pass` it replaced. Each test drives the real failure path
and asserts (a) the failure is recorded and (b) the best-effort behaviour is
unchanged, since the whole reason the handler exists is that the caller must
not be blocked or taken down by the failure.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import tempfile

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("EMBEDDER_MODE", "hash")

from server.core.memory_engine import MemoryEngine
from server.core.types import SessionStatus


@pytest_asyncio.fixture
async def engine():
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    eng = MemoryEngine(db_path=db_path, embedder_mode="hash", short_term_max=10)
    await eng.initialize()
    yield eng
    await eng.shutdown()
    if os.path.exists(db_path):
        os.unlink(db_path)


async def _store(engine: MemoryEngine, content: str) -> None:
    await engine.store(content=content, memory_type="episodic")


@pytest.mark.asyncio
async def test_auto_checkpoint_loop_logs_failure_and_keeps_scheduling(monkeypatch, caplog):
    """A failing summarization pass must be logged, and the loop must keep
    running instead of dying — that is the promise the swallow made."""
    from server.commands import auto_checkpoint as ac

    calls = 0

    async def broken(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("summarizer exploded")

    monkeypatch.setattr(ac, "create_delta_checkpoint", broken)

    with caplog.at_level(logging.ERROR, logger="levh.auto_checkpoint"):
        task = asyncio.create_task(
            ac._background_loop(
                object(), agent="t", session_id=None, project=None, interval=0.01
            )
        )
        await asyncio.sleep(0.08)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert calls >= 2, "the loop stopped after one failure instead of retrying"
    assert any(
        "auto-checkpoint pass failed" in r.message for r in caplog.records
    ), "the failed pass was swallowed silently"


@pytest.mark.asyncio
async def test_end_session_logs_summarization_failure_and_still_ends(
    engine, monkeypatch, caplog
):
    """Summarization is best-effort: a failure is logged, the session still
    ends."""
    engine.auto_summarize = True
    session = await engine.create_session("logging-fixture")
    await _store(engine, "a memory for the session")

    async def broken(*_args, **_kwargs):
        raise RuntimeError("summarize blew up")

    monkeypatch.setattr(engine, "summarize_session", broken)

    with caplog.at_level(logging.ERROR, logger="levh.sessions"):
        ended = await engine.end_session(session.id)

    assert ended is not None
    assert ended.status == SessionStatus.ENDED
    assert any(
        "session summarization failed" in r.message for r in caplog.records
    ), "the summarization failure was swallowed silently"


@pytest.mark.asyncio
async def test_shutdown_logs_background_rebuild_failure_and_completes(engine, caplog):
    """Teardown awaits a background rebuild; if it fails, that must be logged
    without stopping the shutdown."""

    async def failing_on_cancel():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            # A real rebuild can fail while cancellable — this is the branch
            # shutdown's `except Exception` exists for.
            raise RuntimeError("background rebuild died") from None

    engine._derived_task = asyncio.create_task(failing_on_cancel())
    await asyncio.sleep(0.01)  # let it reach the sleep

    with caplog.at_level(logging.ERROR, logger="levh.memory_engine"):
        await engine.shutdown()

    assert engine._derived_task is None
    assert any(
        "background derived rebuild failed during shutdown" in r.message
        for r in caplog.records
    ), "the teardown failure was swallowed silently"


@pytest.mark.asyncio
async def test_inline_rebuild_logs_background_failure_before_retrying(engine, caplog):
    """A freshness read serializes against the background pass; when that
    pass failed, the inline retry still runs and the failure is logged."""
    await _store(engine, "seed the derived state")

    async def failing_background():
        raise RuntimeError("background pass failed")

    # Not awaited before the call below, so the task is still pending when
    # `_rebuild_derived_inline` checks it — the production ordering.
    engine._derived_task = asyncio.create_task(failing_background())

    with caplog.at_level(logging.ERROR, logger="levh.memory_engine"):
        await engine._rebuild_derived_inline()

    assert any(
        "background derived refresh failed" in r.message for r in caplog.records
    ), "the background failure was swallowed silently"
    assert engine._derived_dirty is False, "the inline retry did not converge"


@pytest.mark.asyncio
async def test_mcp_lifespan_logs_startup_failures_and_still_starts(
    engine, monkeypatch, caplog, tmp_path
):
    """Presence auto-connect and the continuity brief are both best-effort;
    when they fail, startup proceeds and the failure is recorded."""
    from server.core import engine_provider

    engine_provider.set_engine(engine)
    monkeypatch.setenv("LEVH_AUTO_CONNECT", "1")
    monkeypatch.setenv("LEVH_AUTO_BRIEF", "1")
    monkeypatch.setenv("LEVH_AUTO_CHECKPOINT", "0")

    import server.mcp_stdio as mcp_stdio

    async def broken_connect(*_args, **_kwargs):
        raise RuntimeError("presence unavailable")

    async def broken_brief(*_args, **_kwargs):
        raise RuntimeError("brief unavailable")

    monkeypatch.setattr(mcp_stdio, "smart_auto_connect", broken_connect)
    monkeypatch.setattr(engine, "get_continuity_context", broken_brief)

    with caplog.at_level(logging.ERROR, logger="levh.mcp_stdio"):
        async with mcp_stdio._lifespan(mcp_stdio.mcp):
            pass

    messages = [r.message for r in caplog.records]
    assert any("presence auto-connect failed" in m for m in messages)
    assert any("continuity brief could not be fetched" in m for m in messages)
