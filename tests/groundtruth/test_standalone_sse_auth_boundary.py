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
import pytest_asyncio

from server.core import engine_provider
from server.core.memory_engine import MemoryEngine
from server.core.rate_limit import SlidingWindowRateLimiter


TOKEN = "gt00a5-synthetic-token"
pytestmark = pytest.mark.asyncio(loop_scope="module")


async def _first_http_status(
    app: Any,
    *,
    method: str = "GET",
    path: str = "/sse",
    query_string: bytes = b"",
    headers: list[tuple[bytes, bytes]] | None = None,
    body: bytes = b"",
    client: tuple[str, int] | None = ("127.0.0.1", 0),
) -> int:
    state = {"request_sent": False, "status": None}
    started = asyncio.Event()

    async def receive() -> dict[str, Any]:
        if not state["request_sent"]:
            state["request_sent"] = True
            return {"type": "http.request", "body": body, "more_body": False}
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
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": query_string,
        "root_path": "",
        "headers": [(b"host", b"127.0.0.1:8001"), *(headers or [])],
        "client": client,
        "server": ("127.0.0.1", 8001),
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


@pytest_asyncio.fixture(loop_scope="module")
async def standalone_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Any:
    monkeypatch.setenv("LEVH_TOKEN", TOKEN)
    monkeypatch.setenv("LEVH_MCP_PROFILE", "minimal")
    engine = MemoryEngine(
        db_path=str(tmp_path / "standalone-auth.db"),
        embedder_mode="hash",
    )
    await engine.initialize()
    engine_provider.set_engine(engine)
    module = importlib.reload(importlib.import_module("server.mcp_sse"))
    module.app.limiter = SlidingWindowRateLimiter(100, 60)
    try:
        yield module.app
    finally:
        await engine.shutdown()
        engine_provider.set_engine(None)


async def test_standalone_sse_requires_configured_token(standalone_app: Any) -> None:
    assert await _first_http_status(standalone_app) == 401
    assert await _first_http_status(
        standalone_app,
        headers=[(b"x-levh-token", b"wrong-token")],
    ) == 401
    assert await _first_http_status(
        standalone_app,
        headers=[(b"x-levh-token", TOKEN.encode())],
    ) == 200
    assert await _first_http_status(
        standalone_app,
        headers=[(b"x-stackmemory-token", TOKEN.encode())],
    ) == 200


async def test_standalone_messages_requires_configured_token(
    standalone_app: Any,
) -> None:
    messages_path = "/messages/"
    session_query = b"session_id=not-a-real-session"
    assert await _first_http_status(
        standalone_app,
        method="POST",
        path=messages_path,
        query_string=session_query,
    ) == 401
    assert await _first_http_status(
        standalone_app,
        method="POST",
        path=messages_path,
        query_string=session_query + b"&token=" + TOKEN.encode(),
        headers=[(b"content-type", b"application/json")],
        body=b"{}",
    ) == 400


async def test_standalone_query_token_is_unicode_safe_and_header_fail_closed(
    standalone_app: Any,
) -> None:
    assert await _first_http_status(
        standalone_app,
        query_string=b"token=" + TOKEN.encode(),
    ) == 200
    assert await _first_http_status(
        standalone_app,
        query_string=b"token=" + TOKEN.encode(),
        headers=[(b"x-levh-token", b"wrong-token")],
    ) == 401
    assert await _first_http_status(
        standalone_app,
        query_string=b"token=yanl%C4%B1%C5%9F",
    ) == 401


async def test_standalone_sse_rate_limits_bad_tokens(standalone_app: Any) -> None:
    standalone_app.limiter = SlidingWindowRateLimiter(2, 60)
    assert await _first_http_status(
        standalone_app,
        query_string=b"token=wrong-1",
    ) == 401
    assert await _first_http_status(
        standalone_app,
        query_string=b"token=wrong-2",
    ) == 401
    assert await _first_http_status(
        standalone_app,
        query_string=b"token=wrong-3",
    ) == 429


async def test_standalone_without_token_is_loopback_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LEVH_TOKEN", raising=False)
    monkeypatch.delenv("LEVH_ALLOW_REMOTE_WITHOUT_TOKEN", raising=False)
    monkeypatch.delenv("STACKMEMORY_ALLOW_REMOTE_WITHOUT_TOKEN", raising=False)
    monkeypatch.setenv("LEVH_MCP_PROFILE", "minimal")
    engine = MemoryEngine(
        db_path=str(tmp_path / "standalone-loopback.db"),
        embedder_mode="hash",
    )
    await engine.initialize()
    engine_provider.set_engine(engine)
    module = importlib.reload(importlib.import_module("server.mcp_sse"))
    try:
        assert await _first_http_status(
            module.app,
            client=("127.0.0.1", 1001),
        ) == 200
        assert await _first_http_status(
            module.app,
            client=("::1", 1002),
        ) == 200
        assert await _first_http_status(
            module.app,
            client=("::ffff:127.0.0.1", 1003),
        ) == 200
        assert await _first_http_status(
            module.app,
            client=("203.0.113.5", 1004),
        ) == 401
        assert await _first_http_status(
            module.app,
            client=None,
        ) == 401
    finally:
        await engine.shutdown()
        engine_provider.set_engine(None)
