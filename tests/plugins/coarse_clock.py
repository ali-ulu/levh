"""A deliberately coarse wall clock, as a pytest plugin (issue #379).

Windows CPython builds ``datetime.now()`` on ``GetSystemTimeAsFileTime()``,
whose granularity is 15.625 ms. Two writes that land in the same tick get the
*same* ``created_at``/``valid_from``/``superseded_at`` string. Anything that
treats those strings as a strict (``>``) ordering key — or ``ORDER BY
created_at DESC LIMIT 1`` without a tie-break — then resolves the tie by luck
rather than by write order.

Linux CI hands out microsecond-resolution timestamps, so the whole failure
class is invisible there: the suite is green exactly where it is trusted most.
This plugin quantises ``datetime.now()`` down to the Windows tick so ordering
assumptions are exercised on every interpreter.

Two modes:

``floor`` (default)
    ``now()`` is the real clock floored to the 15.625 ms tick — faithful to a
    Windows interpreter. A test only sees a tie when its writes happen to fall
    inside one tick, so this mode exercises the real machine but is
    *probabilistic*: on a fast machine the offending pair ties most of the
    time, not always.

``lock`` (``--coarse-clock=lock``)
    The first ``now()`` in a test pins that tick for the whole test, so every
    write in a test shares one instant and every tie is guaranteed. As a gate
    this is strictly stronger than a Windows interpreter — a test that takes
    30 ms would see one or two ticks there, not zero — and it is deterministic,
    which a probabilistic tick is not. Code that does not depend on the clock
    advancing passes in both modes.

Load it explicitly — it is not part of the default suite::

    python -m pytest -q -p tests.plugins.coarse_clock tests/test_auto_checkpoint.py
    python -m pytest -q -p tests.plugins.coarse_clock --coarse-clock=lock tests/...

Only ``datetime`` computed *inside* the patch targets is coarse; the real clock
stays untouched everywhere else, and no test data is fabricated.
"""

from __future__ import annotations

import datetime as _datetime
import importlib

# 15.625 ms — the step of GetSystemTimeAsFileTime(), i.e. exactly
# ``datetime.now()`` on a stock Windows CPython 3.12 build. 15625 µs divides a
# second exactly (64 ticks), so flooring the microsecond field is the same
# quantisation as the OS itself performs.
TICK_MICROSECONDS = 15_625

# The modules that stamp wall-clock strings which are later used as an
# ordering or interval key. Patching the module global (not the ``datetime``
# class) is what makes this surgical: every other module keeps the real clock.
PATCH_TARGETS = (
    "server.core.types",
    "server.core.agent_services",
    "server.core.engine.write",
    "server.commands.auto_checkpoint",
)

MODE_FLOOR = "floor"
MODE_LOCK = "lock"


def _floor_to_tick(moment: _datetime.datetime) -> _datetime.datetime:
    return moment.replace(
        microsecond=(moment.microsecond // TICK_MICROSECONDS) * TICK_MICROSECONDS
    )


class _Clock:
    """The mode and, in lock mode, the tick currently pinned."""

    mode = MODE_FLOOR
    locked: _datetime.datetime | None = None


def pytest_addoption(parser) -> None:
    group = parser.getgroup("coarse-clock")
    group.addoption(
        "--coarse-clock",
        action="store",
        default=MODE_FLOOR,
        choices=(MODE_FLOOR, MODE_LOCK),
        dest="coarse_clock",
        help=(
            "coarse-clock plugin mode: 'floor' quantises datetime.now() to the "
            "15.625 ms Windows tick; 'lock' additionally pins one tick per test "
            "so co-tick writes are deterministic (issue #379)."
        ),
    )


class CoarseDatetime(_datetime.datetime):
    """``datetime`` whose ``now()`` is floored to the Windows tick."""

    @classmethod
    def now(cls, tz=None):
        # ``_datetime.datetime.now`` explicitly, not ``super()``: the base
        # classmethod is what actually reads the clock, and calling it by name
        # keeps this a plain override rather than infinite recursion.
        precise = _datetime.datetime.now(tz)
        tick = _floor_to_tick(precise)
        if _Clock.mode == MODE_LOCK:
            if _Clock.locked is None:
                _Clock.locked = tick
            return _Clock.locked
        return tick


def pytest_runtest_setup(item) -> None:
    """Start every test on a fresh tick in lock mode."""
    _Clock.locked = None


def frozen_datetime(
    moment: _datetime.datetime | None = None,
) -> type[_datetime.datetime]:
    """A ``datetime`` subclass whose ``now()`` never advances.

    The deterministic limit of the coarse clock, for a test that wants to
    *prove* it does not depend on two writes being separable: every ``now()``
    in scope returns one instant. Used through :func:`patch_clock`, so only
    the modules under test see it.
    """
    pinned = moment or _datetime.datetime.now(_datetime.timezone.utc)

    class _Frozen(_datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return pinned if tz is not None else pinned.replace(tzinfo=None)

    return _Frozen


def patch_clock(monkeypatch, datetime_class) -> None:
    """Point every patch target's module-level ``datetime`` at ``datetime_class``.

    ``monkeypatch.setattr(module, "datetime", ...)`` — the class, not the
    module — because each target does ``from datetime import datetime`` and
    resolves the name at call time. Undone by pytest at the end of the test.
    """
    for name in PATCH_TARGETS:
        monkeypatch.setattr(importlib.import_module(name), "datetime", datetime_class)


def pytest_configure(config) -> None:
    """Swap in the coarse clock before test modules are collected.

    ``pytest_configure`` runs after the entry-point plugins are registered and
    before collection imports any test module, so every engine built by the
    suite sees the quantised clock. A target that cannot be imported is left
    alone rather than failing the run: the plugin must stay usable by a subset
    of the suite.
    """
    _Clock.mode = config.getoption("coarse_clock")
    for name in PATCH_TARGETS:
        try:
            module = importlib.import_module(name)
        except ImportError:  # pragma: no cover - partial checkouts
            continue
        module.datetime = CoarseDatetime
