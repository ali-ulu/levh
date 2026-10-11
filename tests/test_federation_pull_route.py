"""Dashboard federation pull route tests (#485 UI).

Mirrors test_federation_transport but exercises the server-side POST
/api/federation/pull route instead of the CLI. Beyond the CLI contracts
(scheme check, https-for-token rule, verification-before-import, expected
node pin) the route adds the SSRF boundary CodeQL flagged: the request URL
is built only from the operator-approved LEVH_FEDERATION_PEERS allowlist,
and a body-supplied source can never point the server at another host.
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


def _request(source, expected_node_id=None):
    return data_transfer.FederationPullRequest(
        source=source, expected_node_id=expected_node_id
    )


@pytest.mark.asyncio
async def test_route_requires_allowlist(engine, monkeypatch, tmp_path):
    for name in (
        "LEVH_FEDERATION_PEERS",
        "STACKMEMORY_FEDERATION_PEERS",
        "LEVH_FEDERATION_KEY_PATH",
        "STACKMEMORY_FEDERATION_KEY_PATH",
        "LEVH_TOKEN",
        "STACKMEMORY_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(HTTPException) as exc_info:
        await data_transfer.federation_pull(
            _request("http://peer-a.example:8000"), engine
        )
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_route_rejects_source_outside_allowlist(
    engine, monkeypatch, tmp_path
):
    key = _key(tmp_path)
    await _signed_peer_envelope(tmp_path, key)
    monkeypatch.setenv("LEVH_FEDERATION_KEY_PATH", str(key))
    monkeypatch.setenv(
        "LEVH_FEDERATION_PEERS",
        "http://peer-a.example:8000,http://peer-b.example:8000",
    )
    with pytest.raises(HTTPException) as exc_info:
        await data_transfer.federation_pull(
            _request("http://evil.example:9000"), engine
        )
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_route_pulls_from_allowlisted_peer_and_defaulting_to_sole_entry(
    engine, monkeypatch, tmp_path
):
    key = _key(tmp_path)
    envelope = await _signed_peer_envelope(tmp_path, key)
    captured = {}

    def _get(url, **kwargs):
        captured["url"] = url
        return httpx.Response(200, json=envelope)

    monkeypatch.setenv("LEVH_FEDERATION_KEY_PATH", str(key))
    monkeypatch.delenv("LEVH_TOKEN", raising=False)
    monkeypatch.delenv("STACKMEMORY_TOKEN", raising=False)
    monkeypatch.setenv("LEVH_FEDERATION_PEERS", "HTTP://Peer-A.example:8000/")
    monkeypatch.setattr(httpx, "get", _get)
    result = await data_transfer.federation_pull(
        _request(""), engine
    )
    assert result["imported"] == 1
    assert result["node_id"] == "peer-a"
    # The URL is built from the allowlist entry, not from the request body.
    assert captured["url"] == "HTTP://Peer-A.example:8000/api/v1/federation/envelope"


@pytest.mark.asyncio
async def test_route_rejects_tampered_signature(engine, monkeypatch, tmp_path):
    key = _key(tmp_path)
    envelope = await _signed_peer_envelope(tmp_path, key)
    envelope["bundle"]["memories"] = envelope["bundle"].get("memories", [])
    if envelope["bundle"]["memories"]:
        envelope["bundle"]["memories"][0]["content"] = "tampered content"
    monkeypatch.setenv("LEVH_FEDERATION_KEY_PATH", str(key))
    monkeypatch.setenv(
        "LEVH_FEDERATION_PEERS", "http://peer-a.example:8000"
    )
    monkeypatch.setattr(httpx, "get", lambda url, **kw: httpx.Response(200, json=envelope))
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
    monkeypatch.setenv(
        "LEVH_FEDERATION_PEERS", "http://peer-a.example:8000"
    )
    monkeypatch.setattr(
        httpx, "get", lambda url, **kw: httpx.Response(200, json=envelope)
    )
    with pytest.raises(HTTPException) as exc_info:
        await data_transfer.federation_pull(
            _request("http://peer-a.example:8000", expected_node_id="peer-a"),
            engine,
        )
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_route_refuses_token_over_plain_http_remote_and_allows_localhost(
    engine, monkeypatch, tmp_path
):
    key = _key(tmp_path)
    remote_envelope = await _signed_peer_envelope(tmp_path, key)
    monkeypatch.setenv("LEVH_FEDERATION_KEY_PATH", str(key))
    monkeypatch.setenv(
        "LEVH_FEDERATION_PEERS",
        "http://peer-a.example:8000,http://localhost:8000",
    )
    monkeypatch.setenv("LEVH_TOKEN", "tok")
    monkeypatch.setattr(
        httpx,
        "get",
        lambda url, **kw: httpx.Response(200, json=remote_envelope),
    )

    with pytest.raises(HTTPException) as exc_info:
        await data_transfer.federation_pull(
            _request("http://peer-a.example:8000"), engine
        )
    assert exc_info.value.status_code == 422
    assert "HTTPS" in exc_info.value.detail

    result = await data_transfer.federation_pull(
        _request("http://localhost:8000"), engine
    )
    assert result["imported"] == 1
