"""Event-loop choice for ``levh serve``.

On Windows uvicorn's default ``asyncio`` loop is ``ProactorEventLoop``. Its
accept loop (``asyncio/proactor_events.py::_start_serving``, identical in
CPython 3.12 and 3.13) treats *any* ``OSError`` from ``AcceptEx`` as fatal for
the listener: it logs "Accept failed on a socket" and closes the listening
socket. ``WinError 64`` ("the specified network name is no longer available")
is raised whenever a client drops its connection while it is still being
accepted, which is routine for MCP clients that time out and retry. After one
such drop the process keeps running but refuses every new connection, so every
agent sees ``ECONNREFUSED`` until someone restarts the server.

``SelectorEventLoop`` accepts with plain ``accept()`` on a non-blocking socket;
a per-connection error there never closes the listener. Its trade-offs on
Windows - no asyncio subprocess support and a ``select()`` ceiling of about
500 sockets - do not touch LEVH: the server spawns no asyncio subprocesses and
serves a handful of local clients.
"""

from __future__ import annotations

import asyncio
import sys

# The import string uvicorn resolves through ``Config.get_loop_factory``: a
# value outside its built-in names is imported and used as the loop factory.
SELECTOR_LOOP = "server.core.event_loop:selector_event_loop"


def selector_event_loop() -> asyncio.AbstractEventLoop:
    """Loop factory handed to uvicorn on Windows."""
    return asyncio.SelectorEventLoop()


def serve_loop_setting(platform: str = sys.platform) -> str:
    """The ``loop=`` value for ``uvicorn.run``: selector on Windows, else uvicorn's default."""
    return SELECTOR_LOOP if platform == "win32" else "auto"
