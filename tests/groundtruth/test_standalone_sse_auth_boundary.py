"""Desired invariant for standalone MCP SSE authentication.

The loopback multi-process reproduction is retained under
evidence/groundtruth/task-00A4/harness/. This test invokes the ASGI app
in-process and never opens a listening socket.
"""

import asyncio
from contextlib import suppress
import importlib
from pathlib import Path
from typing import Any

import pytest

from server.core import engine_provider
from server.core.memory_engine import MemoryEngine


P0_4_REASON = (
    "P0-4 confirmed: standalone server.mcp_sse:app does not enforce the "
    "configured LEVH_TOKEN"
)


async def _first_sse_status(app: Any) -> int:
    state = {"request_sent": False, "status": None}
    started = asyncio.Event()

    async def receive() -> dict[str, Any]:
        if not state["request_sent"]:
            state["request_sent"] = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            state["status"] = message["status"]
            started.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/sse",
        "raw_path": b"/sse",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"localhost:8001")],
        "client": ("127.0.0.1", 41000),
        "server": ("localhost", 8001),
        "state": {},
    }
    task = asyncio.create_task(app(scope, receive, send))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        assert state["status"] is not None
        return int(state["status"])
    finally:
        task.cancel()
        with suppress(BaseException):
            await task


@pytest.mark.xfail(strict=True, reason=P0_4_REASON)
@pytest.mark.asyncio
async def test_standalone_sse_requires_configured_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LEVH_TOKEN", "gt00a5-synthetic-token")
    monkeypatch.setenv("LEVH_MCP_PROFILE", "minimal")
    engine = MemoryEngine(
        db_path=str(tmp_path / "standalone-auth.db"),
        embedder_mode="hash",
    )
    engine_provider.set_engine(engine)
    module = importlib.reload(importlib.import_module("server.mcp_sse"))
    try:
        status = await _first_sse_status(module.app)
        assert status == 401
    finally:
        await engine.shutdown()
        engine_provider.set_engine(None)
