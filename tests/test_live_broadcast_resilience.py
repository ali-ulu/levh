"""Regression tests for issue #131: the live WebSocket broadcast must not die
silently when its recorded event loop is closed.

Pre-fix behaviour (reproduced): ``run_coroutine_threadsafe`` into a closed
loop leaked the send coroutine (RuntimeWarning: never awaited), the socket
stayed registered, and new clients kept being scheduled into the dead loop —
the live feed died with no error anywhere. Post-fix: a closed recorded loop
is skipped (no leak, no warning), re-bound when a new client connects, and a
send that fails on the target loop prunes the dead socket via a done-callback.
"""

from __future__ import annotations

import asyncio
import threading
import warnings

import pytest

from server.routes import live_broadcast as lb


class FakeWS:
    def __init__(self, name: str = "ws") -> None:
        self.name = name
        self.sent: list[str] = []

    async def send_text(self, message: str) -> None:
        self.sent.append(message)


@pytest.fixture(autouse=True)
def _reset_registry():
    lb._clients.clear()
    old_loop = lb._loop
    lb._loop = None
    yield
    lb._clients.clear()
    lb._loop = old_loop


def _run_route_on(loop: asyncio.AbstractEventLoop, ws: FakeWS) -> None:
    """Simulate the WS route registering a client on its own loop."""

    async def route() -> None:
        lb.set_event_loop_if_unset()
        lb.ws_clients().add(ws)

    loop.run_until_complete(route())


def _loop_on_thread(ws: FakeWS) -> asyncio.AbstractEventLoop:
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=_run_route_on, args=(loop, ws))
    thread.start()
    thread.join()
    return loop


@pytest.mark.asyncio
async def test_fan_out_into_closed_loop_never_leaks_a_coroutine():
    """The issue's exact shape: recorded loop closed, engine event fires.
    No coroutine may be scheduled into the dead loop (no RuntimeWarning), and
    the fan-out must not crash the engine's event listener."""
    ws = FakeWS("stale")
    dead_loop = asyncio.new_event_loop()
    lb._loop = dead_loop
    dead_loop.close()
    lb._clients.add(ws)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        lb._on_engine_event("stored", {"id": "m1"})
        await asyncio.sleep(0.05)  # let any leaked coroutine surface

    leaked = [w for w in caught if "never awaited" in str(w.message)]
    assert leaked == [], f"coroutine leaked into closed loop: {leaked}"
    assert ws.sent == []


@pytest.mark.asyncio
async def test_new_client_rebinds_after_loop_restart():
    """Server cycle: clients were registered on loop A, loop A closed, a new
    client connects on loop B. The registry must re-bind to B so the new
    client receives events."""
    stale = FakeWS("stale")
    dead_loop = _loop_on_thread(stale)  # registers stale + records loop A
    dead_loop.close()

    fresh = FakeWS("fresh")
    lb.set_event_loop_if_unset()  # re-bind to the running loop
    assert lb._loop is asyncio.get_running_loop()

    lb.ws_clients().add(fresh)
    lb._on_engine_event("stored", {"id": "m1"})
    await asyncio.sleep(0.05)

    assert fresh.sent, "new client got no event after loop restart"
    assert '"stored"' in fresh.sent[0]


@pytest.mark.asyncio
async def test_failed_send_prunes_dead_socket_via_done_callback():
    """A send that fails on the live loop (socket gone mid-flight) must prune
    the socket — done-callback path, not the synchronous RuntimeError path."""
    class DeadWS(FakeWS):
        async def send_text(self, message: str) -> None:
            raise RuntimeError("socket closed")

    dead = DeadWS("dead")
    alive = FakeWS("alive")
    lb._loop = asyncio.get_running_loop()
    lb._clients.update({dead, alive})

    lb._on_engine_event("stored", {"id": "m1"})
    await asyncio.sleep(0.05)  # let the done-callbacks run

    assert dead not in lb._clients, "dead socket not pruned"
    assert alive in lb._clients
    assert len(alive.sent) == 1


@pytest.mark.asyncio
async def test_synchronous_runtime_error_still_prunes():
    """The prune path (run_coroutine_threadsafe raises before scheduling)
    keeps working: a loop that closes between the is_closed() check and the
    send prunes the socket whose send raced the close — and the coroutine is
    closed explicitly, so nothing leaks."""
    dead = FakeWS("dead")
    real_loop = asyncio.get_running_loop()
    lb._loop = real_loop
    lb._clients.add(dead)

    class RaceLoop:
        """Wraps the live loop; the first call_soon_threadsafe raises."""

        def __init__(self, target: asyncio.AbstractEventLoop) -> None:
            self._target = target

        def __getattr__(self, name):
            return getattr(self._target, name)

        def call_soon_threadsafe(self, *args, **kwargs):
            raise RuntimeError("Event loop is closed")

    lb._loop = RaceLoop(real_loop)  # type: ignore[assignment]
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            lb._on_engine_event("stored", {"id": "m1"})
            await asyncio.sleep(0.05)
    finally:
        lb._loop = real_loop

    leaked = [w for w in caught if "never awaited" in str(w.message)]
    assert leaked == [], "coroutine from the raced send leaked"
    assert dead not in lb._clients, "raced dead socket not pruned"


@pytest.mark.asyncio
async def test_healthy_broadcast_unchanged():
    """Happy path: two live clients both receive the event, neither pruned."""
    a, b = FakeWS("a"), FakeWS("b")
    lb._loop = asyncio.get_running_loop()
    lb._clients.update({a, b})

    lb._on_engine_event("stored", {"id": "m1"})
    await asyncio.sleep(0.05)

    assert len(a.sent) == 1 and len(b.sent) == 1
    assert {a, b} <= lb._clients


@pytest.mark.asyncio
async def test_no_clients_short_circuits_before_loop_check():
    """Empty registry: fan-out is a no-op even with no loop recorded."""
    lb._loop = None
    lb._on_engine_event("stored", {"id": "m1"})  # must not raise


@pytest.mark.asyncio
async def test_live_loop_recorded_is_not_rebound():
    """The rebind is closed-loop-only: a live recorded loop stays authoritative
    even when a route handler runs on a different loop (threaded fan-out)."""
    live_loop = asyncio.get_running_loop()
    lb._loop = live_loop
    lb.set_event_loop_if_unset()
    assert lb._loop is live_loop
