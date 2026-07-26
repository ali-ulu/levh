from __future__ import annotations

import logging
from typing import Any

import pytest

from server.auth import (
    ALLOW_REMOTE_WITHOUT_TOKEN_ENV,
    RemoteAccessBoundaryMiddleware,
    remote_without_token_allowed,
)
from server.core.rate_limit import SlidingWindowRateLimiter


REMOTE_CLIENT = ("203.0.113.5", 41000)
LOOPBACK_CLIENT = ("127.0.0.1", 41001)


async def _downstream(scope: dict, receive: Any, send: Any) -> None:
    if scope["type"] == "http":
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})
    elif scope["type"] == "websocket":
        await send({"type": "websocket.accept"})


async def _http_status(
    app: Any,
    *,
    path: str = "/",
    client: tuple[str, int] | None = REMOTE_CLIENT,
) -> int:
    messages: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        messages.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "root_path": "",
            "headers": [(b"host", b"test")],
            "client": client,
            "server": ("127.0.0.1", 8000),
        },
        receive,
        send,
    )
    return next(
        int(message["status"])
        for message in messages
        if message["type"] == "http.response.start"
    )


async def _websocket_message(app: Any, *, client: tuple[str, int] | None) -> dict:
    messages: list[dict] = []

    async def receive() -> dict:
        return {"type": "websocket.connect"}

    async def send(message: dict) -> None:
        messages.append(message)

    await app(
        {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "scheme": "ws",
            "path": "/ws/memory",
            "raw_path": b"/ws/memory",
            "query_string": b"",
            "root_path": "",
            "headers": [(b"host", b"test")],
            "client": client,
            "server": ("127.0.0.1", 8000),
            "subprotocols": [],
        },
        receive,
        send,
    )
    return messages[0]


def _clear_remote_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ALLOW_REMOTE_WITHOUT_TOKEN_ENV, raising=False)
    monkeypatch.delenv("STACKMEMORY_ALLOW_REMOTE_WITHOUT_TOKEN", raising=False)


@pytest.mark.asyncio
async def test_tokenless_boundary_allows_loopback_and_rejects_remote_paths(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _clear_remote_override(monkeypatch)
    boundary = RemoteAccessBoundaryMiddleware(
        _downstream,
        token="",
        warning_limiter=SlidingWindowRateLimiter(1, 60),
    )
    caplog.set_level(logging.WARNING, logger="levh.auth")

    assert await _http_status(boundary, client=LOOPBACK_CLIENT) == 204
    for path in (
        "/",
        "/docs",
        "/openapi.json",
        "/api/health",
        "/api/stats",
        "/api/mcp/sse",
    ):
        assert await _http_status(boundary, path=path) == 401

    warnings = [
        record.getMessage()
        for record in caplog.records
        if "rejected remote request" in record.getMessage()
    ]
    assert len(warnings) == 1
    assert "203.0.113.5" in warnings[0]
    assert "LEVH_ALLOW_REMOTE_WITHOUT_TOKEN=true" in warnings[0]


@pytest.mark.asyncio
async def test_tokenless_boundary_rejects_unknown_peer_and_remote_websocket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_remote_override(monkeypatch)
    boundary = RemoteAccessBoundaryMiddleware(
        _downstream,
        token="",
        warning_limiter=SlidingWindowRateLimiter(10, 60),
    )

    assert await _http_status(boundary, client=None) == 401
    assert await _websocket_message(boundary, client=REMOTE_CLIENT) == {
        "type": "websocket.close",
        "code": 1008,
        "reason": "remote access requires LEVH_TOKEN",
    }
    assert await _websocket_message(boundary, client=LOOPBACK_CLIENT) == {
        "type": "websocket.accept",
    }


@pytest.mark.asyncio
async def test_explicit_remote_override_allows_remote_and_warns_once_at_startup(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv(ALLOW_REMOTE_WITHOUT_TOKEN_ENV, "true")
    boundary = RemoteAccessBoundaryMiddleware(_downstream, token="")
    caplog.set_level(logging.WARNING, logger="levh.auth")

    async def unused_receive() -> dict:
        raise AssertionError("downstream lifespan stub must not receive")

    async def unused_send(_message: dict) -> None:
        raise AssertionError("downstream lifespan stub must not send")

    await boundary({"type": "lifespan"}, unused_receive, unused_send)
    await boundary({"type": "lifespan"}, unused_receive, unused_send)

    assert await _http_status(boundary) == 204
    assert await _websocket_message(boundary, client=REMOTE_CLIENT) == {
        "type": "websocket.accept",
    }
    warnings = [
        record.getMessage()
        for record in caplog.records
        if "unauthenticated remote access is enabled" in record.getMessage()
    ]
    assert len(warnings) == 1
    assert ALLOW_REMOTE_WITHOUT_TOKEN_ENV in warnings[0]


@pytest.mark.parametrize("value", ["", "0", "false", "off", "no", "garbage"])
def test_remote_override_is_fail_closed_for_non_truthy_values(
    value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ALLOW_REMOTE_WITHOUT_TOKEN_ENV, value)
    assert remote_without_token_allowed() is False


@pytest.mark.asyncio
async def test_configured_token_defers_remote_boundary_to_existing_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ALLOW_REMOTE_WITHOUT_TOKEN_ENV, "false")
    boundary = RemoteAccessBoundaryMiddleware(_downstream, token="configured")

    assert await _http_status(boundary) == 204
    assert await _websocket_message(boundary, client=REMOTE_CLIENT) == {
        "type": "websocket.accept",
    }


def test_main_api_registers_remote_access_boundary() -> None:
    import server.api as api_mod

    registrations = [
        middleware
        for middleware in api_mod.app.user_middleware
        if middleware.cls is RemoteAccessBoundaryMiddleware
    ]
    assert len(registrations) == 1
    assert registrations[0].kwargs["token"] == api_mod._API_TOKEN
