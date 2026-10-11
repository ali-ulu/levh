"""Dashboard federation pull route tests (#485 UI).

Mirrors test_federation_transport but exercises the server-side POST
/api/federation/pull route instead of the CLI: the receiver engine lives in
one process, the peer envelope is fetched through a monkeypatched httpx.get,
and every security contract of the CLI (scheme check, https-for-token rule,
verification-before-import, expected node pin) must hold here too.
"""

from __future__ import annotations

import os

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException
os.environ["EMBEDDER_MODE"] = "hash"

from server.core.federation import sign_envelope
from server.core.full_export import build_full_export
from server.core.memory_engine import MemoryEngine
from server.routes import data_transfer


@pytest_asyncio.fixture
async def engine(tmp_path):
    eng = MemoryEngine(
        db_path=str(tmp_path / "receiver.db"),
        embedder_mode="hash",
        short_term_max=10,
    )
    await eng.initialize()
    yield eng
    await eng.shutdown()


def _key(tmp_path, name="peer.key"):
    path = tmp_path / name
    path.write_text("operator-shared-secret", encoding="utf-8")
    return path


def _signed_peer_envelope(tmp_path, key, *, node_id="peer-a"):
    async def _run():
        # Reuse the running loop under pytest-asyncio instead of asyncio.run,
        # which would raise inside an async test.
        src = MemoryEngine(
            db_path=str(tmp_path / "peer.db"),
            embedder_mode="hash",
            short_term_max=10,
        )
        await src.initialize()
        try:
            await src.store(
                "the peer deploy branch is federation-prod",
                memory_type="episodic",
            )
            bundle = await build_full_export(src)
        finally:
            await src.shutdown()
        return sign_envelope(
            bundle,
            node_id=node_id,
            key_path=str(key),
            algorithm="hmac-sha256",
        )

    return _run()


async def _await(maybe_coro):
    """Await a coroutine on the active loop (pytest-asyncio provides one)."""
    return await maybe_coro


def _request(source, expected_node_id=None):
    return data_transfer.FederationPullRequest(
        source=source, expected_node_id=expected_node_id
    )


@pytest.mark.asyncio
async def test_route_rejects_non_http_source(engine):
    with pytest.raises(HTTPException) as exc_info:
        await data_transfer.federation_pull(
            _request("ftp://peer.example/envelope"), engine
        )
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_route_requires_receiver_key_configured(engine, monkeypatch, tmp_path):
    for name in (
        "LEVH_FEDERATION_KEY_PATH",
        "STACKMEMORY_FEDERATION_KEY_PATH",
        "LEVH_TOKEN",
        "STACKMEMORY_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    key = _key(tmp_path)
    envelope = await _signed_peer_envelope(tmp_path, key)
    with pytest.raises(HTTPException) as exc_info:
        await data_transfer.federation_pull(
            _request("http://peer-a.example:8000"), engine
        )
    assert exc_info.value.status_code == 503
    assert envelope["node_id"] == "peer-a"


@pytest.mark.asyncio
async def test_route_pulls_verifies_and_imports(engine, monkeypatch, tmp_path):
    key = _key(tmp_path)
    envelope = await _signed_peer_envelope(tmp_path, key)
    monkeypatch.setenv("LEVH_FEDERATION_KEY_PATH", str(key))
    monkeypatch.delenv("LEVH_TOKEN", raising=False)
    monkeypatch.delenv("STACKMEMORY_TOKEN", raising=False)
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _ok(envelope))
    result = await data_transfer.federation_pull(
        _request("http://peer-a.example:8000"), engine
    )
    assert result["imported"] == 1
    assert result["node_id"] == "peer-a"
    assert result["algorithm"] == "hmac-sha256"


def _ok(envelope):
    return httpx.Response(200, json=envelope)


@pytest.mark.asyncio
async def test_route_rejects_tampered_signature(engine, monkeypatch, tmp_path):
    key = _key(tmp_path)
    envelope = await _signed_peer_envelope(tmp_path, key)
    envelope["bundle"]["memories"] = envelope["bundle"].get("memories", [])
    if envelope["bundle"]["memories"]:
        envelope["bundle"]["memories"][0]["content"] = "tampered content"
    monkeypatch.setenv("LEVH_FEDERATION_KEY_PATH", str(key))
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _ok(envelope))
    with pytest.raises(HTTPException) as exc_info:
        await data_transfer.federation_pull(
            _request("http://peer-a.example:8000"), engine
        )
    assert exc_info.value.status_code == 422
    assert "rejected envelope" in exc_info.value.detail


@pytest.mark.asyncio
async def test_route_enforces_expected_node_id(engine, monkeypatch, tmp_path):
    key = _key(tmp_path)
    envelope = await _signed_peer_envelope(tmp_path, key, node_id="peer-b")
    monkeypatch.setenv("LEVH_FEDERATION_KEY_PATH", str(key))
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _ok(envelope))
    with pytest.raises(HTTPException) as exc_info:
        await data_transfer.federation_pull(
            _request("http://peer-a.example:8000", expected_node_id="peer-a"),
            engine,
        )
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_route_refuses_token_over_plain_http_remote(engine, monkeypatch, tmp_path):
    key = _key(tmp_path)
    envelope = await _signed_peer_envelope(tmp_path, key)
    monkeypatch.setenv("LEVH_FEDERATION_KEY_PATH", str(key))
    monkeypatch.setenv("LEVH_TOKEN", "tok")
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _ok(envelope))
    with pytest.raises(HTTPException) as exc_info:
        await data_transfer.federation_pull(
            _request("http://peer-a.example:8000"), engine
        )
    assert exc_info.value.status_code == 422
    assert "HTTPS" in exc_info.value.detail


@pytest.mark.asyncio
async def test_route_allows_token_over_localhost_http(engine, monkeypatch, tmp_path):
    key = _key(tmp_path)
    envelope = await _signed_peer_envelope(tmp_path, key)
    monkeypatch.setenv("LEVH_FEDERATION_KEY_PATH", str(key))
    monkeypatch.setenv("LEVH_TOKEN", "tok")
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _ok(envelope))
    result = await data_transfer.federation_pull(
        _request("http://localhost:8000"), engine
    )
    assert result["imported"] == 1
