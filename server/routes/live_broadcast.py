"""Live WebSocket broadcast registry.

Owns the set of connected ``/ws/memory`` clients and the event loop they run
on. ``server.api`` subscribes the engine's event stream to ``fan_out`` at
startup; the socket lifecycle lives in ``server.routes.live``.

This replaces the old ``api._ws_clients`` / ``api._event_loop`` module
globals (issue #93): the registry is now a single owned object instead of
private names other modules reached into via ``server.api``.

Loop ownership (issue #131): the recorded loop is the one live sockets were
accepted on. When that loop is closed — server restart cycle, test teardown —
the registry must not keep fanning out into it: ``run_coroutine_threadsafe``
on a closed loop leaks the coroutine (never awaited, silent RuntimeWarning)
and every send dies unseen. So ``set_event_loop_if_unset`` re-binds to the
current loop when the recorded one is closed, and ``_on_engine_event`` skips
sockets on a closed loop instead of scheduling into it.

Pruning: a send that never gets scheduled (closed loop) or fails on the
target loop (socket gone mid-send) must remove the socket from the registry —
a dead socket left registered turns every engine event into leaked work.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastapi import WebSocket

    from server.core.memory_engine import MemoryEngine

logger = logging.getLogger("levh.live_broadcast")

_clients: set["WebSocket"] = set()
_loop: asyncio.AbstractEventLoop | None = None


def ws_clients() -> set["WebSocket"]:
    """The connected live-feed sockets (for add/discard by the WS route)."""
    return _clients


def set_event_loop_if_unset() -> None:
    """Remember the loop the WebSocket route is running on.

    Re-binds when the recorded loop is closed (issue #131): a registry pinned
    to a dead loop silently kills the live feed for every client that connects
    afterwards — their sends are scheduled into a loop nobody runs.
    """
    global _loop
    current = asyncio.get_running_loop()
    if _loop is None or _loop.is_closed():
        _loop = current


def subscribe_broadcaster(engine: "MemoryEngine") -> None:
    """Wire the engine's event stream to :func:`fan_out` (idempotent per engine)."""
    if getattr(engine, "_levh_broadcast_attached", False):
        return
    engine.subscribe(_on_engine_event)
    engine._levh_broadcast_attached = True


def _send_scheduled(ws: "WebSocket", message: str) -> bool:
    """Schedule one send onto the target loop; False means the socket is dead.

    Two dead-socket shapes (issue #131):
    - ``run_coroutine_threadsafe`` raises (loop closed) — schedule now, prune now
    - it returns a future that later fails — pruned by the done-callback
    """
    coroutine = ws.send_text(message)
    try:
        future = asyncio.run_coroutine_threadsafe(coroutine, _loop)
    except RuntimeError:
        coroutine.close()  # never scheduled: close it, no leaked-coroutine warning
        return False
    future.add_done_callback(lambda f: _send_finished(ws, f))
    return True


def _send_finished(ws: "WebSocket", future: "asyncio.Future") -> None:
    """Prune the socket when its send failed or was cancelled."""
    try:
        future.result()
    except Exception:  # noqa: BLE001 — a dead socket must never break fan-out
        _clients.discard(ws)


def _on_engine_event(event: str, payload: dict) -> None:
    """Engine event listener → fan out to connected WebSocket clients."""
    if not _clients:
        return
    message = json.dumps({"type": "event", "event": event, "payload": payload}, default=str)
    loop = _loop
    if loop is None or loop.is_closed():
        logger.debug("live broadcast skipped: target loop closed, %d client(s)", len(_clients))
        return
    for ws in list(_clients):
        if not _send_scheduled(ws, message):
            _clients.discard(ws)
