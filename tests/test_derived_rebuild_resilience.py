"""Regression tests for issue #136: a persistently failing derived-state
rebuild must not spin hot. The old code swallowed the error, left
``_derived_dirty`` set, and self-rescheduled unconditionally in ``finally`` —
~100k retries/second with no log line. Now failures back off with a cap, are
logged, and recovery resets the retry counter.

Scenario mirrors production (issue #102 wiring): a freshness-required read
calls ``recompute_derived_state`` -> inline rebuild -> on failure the
``finally`` block hands off to the background task via
``_ensure_derived_state``, which keeps retrying while the flag is dirty.
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


@pytest_asyncio.fixture
async def engine():
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    eng = MemoryEngine(db_path=db_path, embedder_mode="hash", short_term_max=50)
    await eng.initialize()
    yield eng
    for task in (eng._derived_task,):
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    await eng.shutdown()
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.mark.asyncio
async def test_persistent_failure_backs_off_and_logs(engine, caplog):
    """The production failure flow — freshness read, inline rebuild fails,
    background self-reschedule — retries a bounded number of times in the
    window, each failure logged; never a hot loop."""
    calls = 0

    async def broken():
        nonlocal calls
        calls += 1
        raise RuntimeError("corrupt row")

    engine.reindex_entities = broken  # type: ignore[method-assign]
    engine._mark_derived_dirty()
    await engine._ensure_derived_state()  # the finally-block handoff path

    with caplog.at_level(logging.WARNING, logger="levh.memory_engine"):
        await asyncio.sleep(1.2)

    # Without backoff this counter explodes (~100k in 0.5s pre-fix). With
    # capped backoff (0.5 + 1 + 2 + ... seconds) 1.2s allows only a few.
    assert 1 <= calls <= 4, f"hot loop suspected: {calls} retries in 1.2s"
    assert any("derived-state rebuild failed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_failure_logged_with_traceback(engine, caplog):
    """The swallowed exception surfaces: message + stack trace are logged."""
    async def broken():
        raise RuntimeError("corrupt row")

    engine.reindex_entities = broken  # type: ignore[method-assign]
    engine._mark_derived_dirty()
    await engine._ensure_derived_state()

    with caplog.at_level(logging.WARNING, logger="levh.memory_engine"):
        await asyncio.sleep(0.2)

    record = next(
        (r for r in caplog.records if "derived-state rebuild failed" in r.message),
        None,
    )
    assert record is not None
    assert record.exc_info is not None
    assert "corrupt row" in str(record.exc_info[1])


@pytest.mark.asyncio
async def test_recovery_resets_retry_backoff(engine):
    """Once the cause goes away, the fresh write resets the backoff counter
    and triggers an immediate rebuild that succeeds — recovery is fast, not
    gated on the old backoff sleep (issue #139)."""
    fail = True
    calls = 0

    async def flaky():
        nonlocal calls
        calls += 1
        if fail:
            raise RuntimeError("transient")
        return {"entities": 0}

    engine.reindex_entities = flaky  # type: ignore[method-assign]
    engine._mark_derived_dirty()
    await engine._ensure_derived_state()
    await asyncio.sleep(0.7)  # let at least one failure + backoff elapse
    assert engine._derived_retry_count >= 1

    fail = False
    engine._mark_derived_dirty()  # the fresh write: reset + trigger
    for _ in range(200):
        if not engine._derived_dirty and engine._derived_retry_count == 0:
            break
        await asyncio.sleep(0.05)
    assert engine._derived_dirty is False
    assert engine._derived_retry_count == 0


@pytest.mark.asyncio
async def test_success_still_converges(engine):
    """The happy path is untouched: a dirty mark plus the scheduling handoff
    leads to clean derived state with no retry debt."""
    engine._mark_derived_dirty()
    await engine._ensure_derived_state()
    for _ in range(200):
        if not engine._derived_dirty:
            break
        await asyncio.sleep(0.05)
    assert engine._derived_dirty is False
    assert engine._refreshing_derived is False
    assert engine._derived_retry_count == 0


@pytest.mark.asyncio
async def test_freshness_read_converges_through_inline_path(engine):
    """A freshness-required read (the recompute path) still returns fresh
    state when the steps succeed — the fix changes failure handling only."""
    engine._mark_derived_dirty()
    await engine.list_entities_graph(limit=5)
    assert engine._derived_dirty is False
    assert engine._derived_retry_count == 0


# ── Issue #139: the freshness contract ──────────────────────────────


@pytest.mark.asyncio
async def test_freshness_read_raises_when_rebuild_fails(engine):
    """'Return when it is fresh' must not silently hand back stale rows
    (issue #139): a failing step surfaces as an exception to the caller that
    acts on the verdict."""
    async def broken():
        raise RuntimeError("corrupt row")

    engine.reindex_entities = broken  # type: ignore[method-assign]
    engine._mark_derived_dirty()
    # The inline pass re-raises the root cause directly; the wrapper's
    # staleness guard is the backstop when the root cause hides.
    with pytest.raises((RuntimeError, Exception), match="corrupt row|stale"):
        await engine.recompute_derived_state()
    assert engine._derived_dirty is True


@pytest.mark.asyncio
async def test_freshness_read_fails_fast_no_backoff_block(engine):
    """A freshness read must not block on the background retry backoff
    (issue #139: 8s hangs). Inline failure is immediate."""
    async def broken():
        raise RuntimeError("corrupt row")

    engine.reindex_entities = broken  # type: ignore[method-assign]
    engine._mark_derived_dirty()
    await engine._ensure_derived_state()
    await asyncio.sleep(0.1)  # background pass fails, enters backoff sleep

    loop = asyncio.get_running_loop()
    start = loop.time()
    with pytest.raises(RuntimeError):
        engine._derived_dirty = True
        await engine.recompute_derived_state()
    elapsed = loop.time() - start
    assert elapsed < 0.5, f"freshness read blocked {elapsed:.2f}s on backoff"


@pytest.mark.asyncio
async def test_fresh_write_interrupts_backoff_and_resets_counter(engine):
    """A fresh write resets the retry counter (comment now truthful) and the
    pending backoff sleep is interrupted — recovery within milliseconds."""
    fail = True

    async def flaky():
        if fail:
            raise RuntimeError("transient")
        return {"entities": 0}

    engine.reindex_entities = flaky  # type: ignore[method-assign]
    engine._mark_derived_dirty()
    await engine._ensure_derived_state()
    await asyncio.sleep(0.3)
    assert engine._derived_retry_count >= 1

    fail = False
    loop = asyncio.get_running_loop()
    start = loop.time()
    engine._mark_derived_dirty()  # the fresh write
    assert engine._derived_retry_count == 0, "counter not reset by fresh write"
    for _ in range(200):
        if not engine._derived_dirty:
            break
        await asyncio.sleep(0.02)
    elapsed = loop.time() - start
    assert engine._derived_dirty is False
    assert elapsed < 1.0, f"recovery took {elapsed:.2f}s — backoff not interrupted"
