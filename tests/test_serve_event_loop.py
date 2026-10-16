"""``levh serve`` must not run on the Windows proactor loop.

The proactor accept loop closes the listening socket on the first accept
error (WinError 64 when a client drops mid-accept), leaving a live process that
refuses every connection. See ``server/core/event_loop.py``.
"""

from __future__ import annotations

import argparse
import asyncio
import socket
import struct
import sys

import pytest
import uvicorn
from uvicorn.config import Config

from server.core.event_loop import SELECTOR_LOOP, selector_event_loop, serve_loop_setting

# struct linger is two u_short on Windows and two int elsewhere.
_LINGER_ZERO = struct.pack("HH" if sys.platform == "win32" else "ii", 1, 0)


def test_windows_gets_the_selector_loop():
    assert serve_loop_setting("win32") == SELECTOR_LOOP


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_other_platforms_keep_uvicorn_default(platform):
    assert serve_loop_setting(platform) == "auto"


def test_uvicorn_resolves_the_import_string_to_a_selector_loop():
    # Guards the string itself: a typo would only surface at server start.
    factory = Config(app="server.api:app", loop=SELECTOR_LOOP).get_loop_factory()
    assert factory is selector_event_loop
    loop = factory()
    try:
        assert isinstance(loop, asyncio.SelectorEventLoop)
    finally:
        loop.close()


def test_cmd_serve_passes_the_platform_loop(monkeypatch):
    import server.cli as cli
    import server.commands.diagnostics as diagnostics

    # cmd_serve reconfigures global logging; keep that out of the test session.
    monkeypatch.setattr(diagnostics, "install_logging", lambda: None)
    monkeypatch.setattr(diagnostics, "install_access_log_filters", lambda: None)
    called = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: called.append(kw))
    args = argparse.Namespace(host="127.0.0.1", port=8000, reload=False)
    assert cli.cmd_serve(args) == 0
    assert called[0]["loop"] == serve_loop_setting()


def _listener_survives_resets(loop: asyncio.AbstractEventLoop, resets: int = 20) -> bool:
    """Reset ``resets`` connections before they are accepted, then try a clean one.

    True when the clean client gets its answer, i.e. the listener survived.
    """

    async def scenario() -> bool:
        async def handle(reader, writer):
            try:
                if await reader.readline() == b"ping\n":
                    writer.write(b"pong\n")
                    await writer.drain()
            except OSError:
                pass  # the reset connections land here
            finally:
                writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            for _ in range(resets):
                rude = socket.socket()
                rude.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, _LINGER_ZERO)
                rude.connect(("127.0.0.1", port))
                rude.close()  # SO_LINGER 0 -> RST instead of FIN
            await asyncio.sleep(0.2)
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection("127.0.0.1", port), timeout=5
                )
                writer.write(b"ping\n")
                answer = await asyncio.wait_for(reader.readline(), timeout=5)
                writer.close()
            except (OSError, asyncio.TimeoutError):
                return False
            return answer == b"pong\n"
        finally:
            server.close()
            await server.wait_closed()

    # The proactor reports the fatal accept error through the handler; keep it quiet.
    loop.set_exception_handler(lambda _loop, _context: None)
    try:
        return loop.run_until_complete(scenario())
    finally:
        loop.close()


def test_selector_listener_survives_clients_that_reset_before_accept():
    assert _listener_survives_resets(selector_event_loop())


@pytest.mark.skipif(sys.platform != "win32", reason="the proactor loop is Windows-only")
def test_proactor_listener_dies_on_the_same_resets():
    # The bug this module works around, demonstrated. If this starts failing,
    # CPython stopped closing the listener on an accept error and
    # server/core/event_loop.py may be removable.
    assert not _listener_survives_resets(asyncio.ProactorEventLoop())
