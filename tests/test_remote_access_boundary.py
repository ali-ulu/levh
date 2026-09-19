from __future__ import annotations

import logging
from typing import Any

import pytest

from server.auth import (
    ALLOW_REMOTE_WITHOUT_TOKEN_ENV,
    RemoteAccessBoundaryMiddleware,
    remote_without_token_allowed,
    unauthenticated_remote_access_enabled,
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
    assert unauthenticated_remote_access_enabled("") is False


@pytest.mark.parametrize("token", ["", None, b""])
def test_remote_open_state_requires_override_and_absent_token(
    token: str | bytes | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ALLOW_REMOTE_WITHOUT_TOKEN_ENV, "true")
    assert unauthenticated_remote_access_enabled(token) is True

    # A configured token gates every non-loopback peer, so the override — even
    # when still set — no longer describes an open boundary. Callers pass the
    # live resolver's value (server.routes.deps.api_token), not the raw env.
    assert unauthenticated_remote_access_enabled("configured") is False
    assert unauthenticated_remote_access_enabled(b"configured") is False


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
    from server.routes import deps

    # The registration passes the live resolver, not a frozen value (issue
    # #132): a frozen token would pin the boundary to the import-time env.
    registered_token = registrations[0].kwargs["token"]
    assert callable(registered_token)
    assert registered_token is deps.api_token


@pytest.mark.asyncio
async def test_boundary_resolves_token_lively(monkeypatch: pytest.MonkeyPatch) -> None:
    """LEVH_TOKEN set after construction must be honored (issue #132)."""
    monkeypatch.setenv(ALLOW_REMOTE_WITHOUT_TOKEN_ENV, "false")
    monkeypatch.delenv("LEVH_TOKEN", raising=False)

    def live_token() -> str:
        import os

        return os.environ.get("LEVH_TOKEN", "")

    boundary = RemoteAccessBoundaryMiddleware(_downstream, token=live_token)

    # No token configured yet: remote requests are rejected.
    assert await _http_status(boundary) == 401
    close = await _websocket_message(boundary, client=REMOTE_CLIENT)
    assert close["type"] == "websocket.close"

    # Token appears after construction (e.g. .env loaded late, uvicorn env): now accepted.
    monkeypatch.setenv("LEVH_TOKEN", "late-secret")
    assert await _http_status(boundary) == 204
    assert await _websocket_message(boundary, client=REMOTE_CLIENT) == {
        "type": "websocket.accept",
    }


@pytest.mark.asyncio
async def test_boundary_accepts_static_token_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plain string/bytes tokens keep working exactly as before."""
    monkeypatch.setenv(ALLOW_REMOTE_WITHOUT_TOKEN_ENV, "false")
    boundary = RemoteAccessBoundaryMiddleware(_downstream, token="configured")

    assert await _http_status(boundary) == 204
    assert boundary.token == b"configured"


# ── The bind host a surface reports must be the one in force (#156) ──


def test_bind_host_prefers_argv_over_the_config_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`levh serve --host 0.0.0.0` binds argv, but config said 127.0.0.1.

    Config alone is not evidence of the bind: this is the gap that let doctor
    print WARN for a server listening on every interface.
    """
    from server.core.runtime_config import configured_bind_host

    monkeypatch.delenv("LEVH_API_HOST", raising=False)
    monkeypatch.delenv("API_HOST", raising=False)

    assert configured_bind_host(argv=["levh", "serve"]) == "127.0.0.1"
    assert configured_bind_host(argv=["levh", "serve", "--host", "0.0.0.0"]) == "0.0.0.0"
    assert configured_bind_host(argv=["uvicorn", "--host=0.0.0.0"]) == "0.0.0.0"
    # An empty value is not a decision, so config keeps deciding.
    assert configured_bind_host(argv=["levh", "serve", "--host", ""]) == "127.0.0.1"


def test_bind_host_falls_back_to_env_then_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Without argv, the existing precedence still applies."""
    import json

    from server.core.runtime_config import configured_bind_host

    cfg_dir = tmp_path / ".stackmemory"
    cfg_dir.mkdir()
    (cfg_dir / "config.json").write_text(json.dumps({"api_host": "10.0.0.7"}))

    monkeypatch.delenv("LEVH_API_HOST", raising=False)
    monkeypatch.delenv("API_HOST", raising=False)
    assert configured_bind_host(argv=["levh"], cwd=tmp_path) == "10.0.0.7"

    monkeypatch.setenv("API_HOST", "10.0.0.9")
    assert configured_bind_host(argv=["levh"], cwd=tmp_path) == "10.0.0.9"


def test_bind_host_survives_a_malformed_config(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """A broken config must not turn `/api/health` into a 500."""
    from server.core.runtime_config import configured_bind_host

    cfg_dir = tmp_path / ".stackmemory"
    cfg_dir.mkdir()
    (cfg_dir / "config.json").write_text("{not json")
    monkeypatch.delenv("LEVH_API_HOST", raising=False)
    monkeypatch.delenv("API_HOST", raising=False)

    assert configured_bind_host(argv=[], cwd=tmp_path) == "127.0.0.1"


def test_health_reports_the_bind_host_in_force(monkeypatch: pytest.MonkeyPatch) -> None:
    """`/api/health` answers what this process was actually told to bind."""
    import asyncio

    from httpx import ASGITransport, AsyncClient

    from server.api import app

    monkeypatch.setattr(
        "sys.argv", ["uvicorn", "server.api:app", "--host", "0.0.0.0", "--port", "8000"]
    )

    async def _get() -> dict:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return (await client.get("/api/health")).json()

    assert asyncio.run(_get())["api_host"] == "0.0.0.0"


def test_doctor_fails_when_argv_binds_non_loopback(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """The #156 case: override set, the process launched with `--host 0.0.0.0`.

    Reading config printed WARN while the socket was open to every interface.
    The bind now comes from argv, so the check reaches FAIL. No server answers
    on the ephemeral port, so the live probe returns None and argv decides.
    """
    import argparse

    from server.cli import cmd_doctor

    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "doctor-156.db"))
    monkeypatch.setenv("EMBEDDER_MODE", "hash")
    monkeypatch.delenv("LEVH_TOKEN", raising=False)
    monkeypatch.setenv(ALLOW_REMOTE_WITHOUT_TOKEN_ENV, "true")
    monkeypatch.delenv("LEVH_API_HOST", raising=False)
    monkeypatch.delenv("API_HOST", raising=False)
    monkeypatch.setenv("API_PORT", "1")  # nothing listens on port 1
    monkeypatch.setattr("sys.argv", ["levh", "serve", "--host", "0.0.0.0"])

    assert cmd_doctor(argparse.Namespace()) == 1
    failure = capsys.readouterr().out
    assert "Remote access" in failure
    assert "FAIL" in failure
    assert "0.0.0.0" in failure
    assert "Verdict: FAIL" in failure


def test_doctor_still_warns_on_the_loopback_default(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """The override on a private bind stays a warning, not a failure."""
    import argparse

    from server.cli import cmd_doctor

    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "doctor-156b.db"))
    monkeypatch.setenv("EMBEDDER_MODE", "hash")
    monkeypatch.delenv("LEVH_TOKEN", raising=False)
    monkeypatch.setenv(ALLOW_REMOTE_WITHOUT_TOKEN_ENV, "true")
    monkeypatch.delenv("LEVH_API_HOST", raising=False)
    monkeypatch.delenv("API_HOST", raising=False)
    monkeypatch.setenv("API_PORT", "1")
    monkeypatch.setattr("sys.argv", ["levh", "serve"])

    assert cmd_doctor(argparse.Namespace()) == 0
    output = capsys.readouterr().out
    assert "Remote access" in output
    assert "WARN" in output
    assert "Verdict: OK" in output


def test_doctor_prefers_what_a_live_server_reports(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """When a server answers, its own bind beats argv and config alike."""
    import argparse
    import http.server
    import threading

    from server.cli import cmd_doctor

    class _Health(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{"status": "ok", "api_host": "0.0.0.0"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # silence the test output
            return None

    server = http.server.HTTPServer(("127.0.0.1", 0), _Health)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "doctor-156c.db"))
        monkeypatch.setenv("EMBEDDER_MODE", "hash")
        monkeypatch.delenv("LEVH_TOKEN", raising=False)
        monkeypatch.setenv(ALLOW_REMOTE_WITHOUT_TOKEN_ENV, "true")
        monkeypatch.delenv("LEVH_API_HOST", raising=False)
        monkeypatch.delenv("API_HOST", raising=False)
        monkeypatch.setenv("API_PORT", str(port))
        # argv claims loopback; only the running server knows better.
        monkeypatch.setattr("sys.argv", ["levh", "serve"])

        assert cmd_doctor(argparse.Namespace()) == 1
        failure = capsys.readouterr().out
        assert "Remote access" in failure
        assert "FAIL" in failure
        assert "0.0.0.0" in failure
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# ─ The probed port must be the one in force, not just the default (#170) ──


def _health_server(source_api_host: str = "0.0.0.0"):
    """A throwaway server answering ``/api/health`` with the given bind."""
    import http.server

    class _Health(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = (
                '{"status": "ok", "api_host": "%s"}' % source_api_host
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # silence the test output
            return None

    return http.server.HTTPServer(("127.0.0.1", 0), _Health)


def test_configured_api_port_prefers_argv_then_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """`--port` is the port the serving process actually obeys."""
    import json

    from server.core.runtime_config import configured_api_port

    cfg_dir = tmp_path / ".stackmemory"
    cfg_dir.mkdir()
    (cfg_dir / "config.json").write_text(json.dumps({"api_port": 9100}))
    # The config file alone is outranked by the env, which the argv outranks.
    assert configured_api_port(argv=["levh", "serve"], cwd=tmp_path) == 9100
    monkeypatch.setenv("API_PORT", "9101")
    assert configured_api_port(argv=["levh", "serve"], cwd=tmp_path) == 9101
    assert configured_api_port(argv=["levh", "serve", "--port", "9102"], cwd=tmp_path) == 9102
    assert configured_api_port(argv=["levh", "serve", "--port=9103"], cwd=tmp_path) == 9103


def test_configured_api_port_ignores_unusable_argv_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """A malformed `--port` falls back rather than failing the check."""
    from server.core.runtime_config import configured_api_port

    monkeypatch.setenv("API_PORT", "9104")
    assert configured_api_port(argv=["levh", "serve", "--port", "abc"], cwd=tmp_path) == 9104
    assert configured_api_port(argv=["levh", "serve", "--port"], cwd=tmp_path) == 9104
    assert configured_api_port(argv=["levh", "serve", "--port", "70000"], cwd=tmp_path) == 9104


def test_doctor_probes_the_port_from_argv(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """The #170 case: the server serves on `--port`, config still says 8000.

    The probe used to ask config's port only, find nothing, and fall back to a
    loopback bind — reporting OK while the socket was open to every interface.
    """
    import argparse
    import threading

    from server.cli import cmd_doctor

    server = _health_server()
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "doctor-170.db"))
        monkeypatch.setenv("EMBEDDER_MODE", "hash")
        monkeypatch.delenv("LEVH_TOKEN", raising=False)
        monkeypatch.setenv(ALLOW_REMOTE_WITHOUT_TOKEN_ENV, "true")
        monkeypatch.delenv("LEVH_API_HOST", raising=False)
        monkeypatch.delenv("API_HOST", raising=False)
        monkeypatch.setenv("API_PORT", "1")  # config's answer, nothing listens
        monkeypatch.setattr("sys.argv", ["levh", "serve", "--port", str(port)])

        assert cmd_doctor(argparse.Namespace()) == 1
        failure = capsys.readouterr().out
        assert "Remote access" in failure
        assert "FAIL" in failure
        assert "0.0.0.0" in failure
        assert "Verdict: FAIL" in failure
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_doctor_probes_the_common_ports_last(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Cross-process `levh doctor` has no server argv, so common ports are tried."""
    from server.commands.doctor import _candidate_ports

    monkeypatch.setenv("API_PORT", "9105")
    monkeypatch.delenv("LEVH_API_HOST", raising=False)
    monkeypatch.setattr("sys.argv", ["levh", "doctor"])

    class _Runtime:
        api_port = 8000

    ports = _candidate_ports(_Runtime())
    assert ports[0] == 9105  # the configured answer is probed first
    assert ports == [9105, 8000, 9000]  # then the conventional ports, once each
