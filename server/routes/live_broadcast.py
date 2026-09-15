"""Live WebSocket broadcast registry.

Owns the set of connected ``/ws/memory`` clients and the event loop they run
on. ``server.api`` subscribes the engine's event stream to ``fan_out`` at
startup; the socket lifecycle lives in ``server.routes.live``.

This replaces the old ``api._ws_clients`` / ``api._event_loop`` module
globals (issue #93): the registry is now a single owned object instead of
private names other modules reached into via ``server.api``.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastapi import WebSocket

    from server.core.memory_engine import MemoryEngine

_clients: set["WebSocket"] = set()
_loop: asyncio.AbstractEventLoop | None = None


def ws_clients() -> set["WebSocket"]:
    """The connected live-feed sockets (for add/discard by the WS route)."""
    return _clients


def set_event_loop_if_unset() -> None:
    """Remember the loop the WebSocket route is running on, once."""
    global _loop
    if _loop is None:
        _loop = asyncio.get_running_loop()


def subscribe_broadcaster(engine: "MemoryEngine") -> None:
    """Wire the engine's event stream to :func:`fan_out` (idempotent per engine)."""
    if getattr(engine, "_levh_broadcast_attached", False):
        return
    engine.subscribe(_on_engine_event)
    engine._levh_broadcast_attached = True


def _on_engine_event(event: str, payload: dict) -> None:
    """Engine event listener → fan out to connected WebSocket clients."""
    if not _clients or _loop is None:
        return
    message = json.dumps({"type": "event", "event": event, "payload": payload}, default=str)
    for ws in list(_clients):
        try:
            asyncio.run_coroutine_threadsafe(ws.send_text(message), _loop)
        except RuntimeError:
            _clients.discard(ws)
