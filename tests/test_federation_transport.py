"""Pull-first federation transport tests (#484).

No real network: the source endpoint signs the existing B0 envelope, while the
receiver command is exercised against a fake HTTP response. The important
contract is that transport never bypasses signature verification or the
admission gate.
"""

from __future__ import annotations

import argparse
import asyncio
import os

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException

os.environ["EMBEDDER_MODE"] = "hash"

from server.commands import data as data_cmd
from server.core import engine_provider
from server.core.federation import sign_envelope, verify_envelope
from server.core.full_export import build_full_export
from server.core.memory_engine import MemoryEngine
from server.routes.data_transfer import export_federation_envelope


@pytest_asyncio.fixture
async def engine(tmp_path):
    eng = MemoryEngine(
        db_path=str(tmp_path / "source.db"),
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

    return asyncio.run(_run())


def _stored(tmp_path):
    async def _run():
        dst = MemoryEngine(
            db_path=str(tmp_path / "receiver.db"),
            embedder_mode="hash",
            short_term_max=10,
        )
        await dst.initialize()
        try:
            return await dst.list_memories(limit=50)
        finally:
            await dst.shutdown()

    return asyncio.run(_run())


@pytest.mark.asyncio
async def test_source_endpoint_requires_explicit_federation_config(engine, monkeypatch):
    for name in (
        "LEVH_FEDERATION_NODE_ID",
        "STACKMEMORY_FEDERATION_NODE_ID",
        "LEVH_FEDERATION_KEY_PATH",
        "STACKMEMORY_FEDERATION_KEY_PATH",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(HTTPException) as exc:
        await export_federation_envelope(engine)

    assert exc.value.status_code == 503
    assert "not configured" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_source_endpoint_serves_a_verifiable_signed_bundle(engine, tmp_path, monkeypatch):
    key = _key(tmp_path)
    await engine.store("source memory for peer pull", memory_type="episodic")
    monkeypatch.setenv("LEVH_FEDERATION_NODE_ID", "workstation")
    monkeypatch.setenv("LEVH_FEDERATION_KEY_PATH", str(key))
    monkeypatch.setenv("LEVH_FEDERATION_ALGORITHM", "hmac-sha256")

    envelope = await export_federation_envelope(engine)

    assert envelope["node_id"] == "workstation"
    bundle = verify_envelope(
        envelope,
        key_path=str(key),
        expected_node_id="workstation",
    )
    assert any(
        row.get("content") == "source memory for peer pull"
        for row in bundle["memories"]
    )


def test_pull_sends_peer_token_verifies_and_imports(tmp_path, monkeypatch):
    key = _key(tmp_path)
    envelope = _signed_peer_envelope(tmp_path, key)
    token_file = tmp_path / "peer.token"
    token_file.write_text("remote-secret", encoding="utf-8")
    captured = {}

    class Response:
        status_code = 200

        def json(self):
            return envelope

    def fake_get(url, *, headers, timeout, follow_redirects):
        captured.update(
            url=url,
            headers=headers,
            timeout=timeout,
            follow_redirects=follow_redirects,
        )
        return Response()

    monkeypatch.setattr(httpx, "get", fake_get)
    receiver_path = tmp_path / "receiver.db"
    receiver = MemoryEngine(
        db_path=str(receiver_path),
        embedder_mode="hash",
        short_term_max=10,
    )
    engine_provider.set_engine(receiver)
    try:
        rc = data_cmd.cmd_federation_pull(
            argparse.Namespace(
                source="https://peer.example/",
                key=str(key),
                from_node="peer-a",
                token_file=str(token_file),
                timeout=12.0,
            )
        )
    finally:
        engine_provider.set_engine(None)

    assert rc == 0
    assert captured["url"] == "https://peer.example/api/v1/federation/envelope"
    assert captured["headers"]["X-LEVH-Token"] == "remote-secret"
    assert captured["timeout"] == 12.0
    assert captured["follow_redirects"] is False

    memories = _stored(tmp_path)
    imported = next(m for m in memories if "federation-prod" in m.content)
    assert imported.metadata["federation"]["verified"] is True
    assert imported.metadata["federation"]["node_id"] == "peer-a"


def test_pull_rejects_tampered_envelope_without_writing(tmp_path, monkeypatch):
    key = _key(tmp_path)
    envelope = _signed_peer_envelope(tmp_path, key)
    envelope["bundle"]["memories"][0]["content"] = "forged over transport"

    class Response:
        status_code = 200

        def json(self):
            return envelope

    monkeypatch.setattr(httpx, "get", lambda *_args, **_kwargs: Response())
    receiver_path = tmp_path / "receiver.db"
    receiver = MemoryEngine(
        db_path=str(receiver_path),
        embedder_mode="hash",
        short_term_max=10,
    )
    engine_provider.set_engine(receiver)
    try:
        rc = data_cmd.cmd_federation_pull(
            argparse.Namespace(
                source="http://peer",
                key=str(key),
                from_node="peer-a",
                token_file="",
                timeout=30.0,
            )
        )
    finally:
        engine_provider.set_engine(None)

    assert rc == 1
    assert receiver_path.exists() is False
    assert _stored(tmp_path) == []


def test_pull_refuses_http_failure_before_opening_store(tmp_path, monkeypatch):
    key = _key(tmp_path)

    class Response:
        status_code = 503

        def json(self):
            return {"detail": "offline"}

    monkeypatch.setattr(httpx, "get", lambda *_args, **_kwargs: Response())

    rc = data_cmd.cmd_federation_pull(
        argparse.Namespace(
            source="http://peer",
            key=str(key),
            from_node="peer-a",
            token_file="",
            timeout=30.0,
        )
    )

    assert rc == 1
    assert not (tmp_path / "receiver.db").exists()


def test_pull_refuses_token_over_plaintext_remote_http(tmp_path, monkeypatch):
    key = _key(tmp_path)
    token_file = tmp_path / "peer.token"
    token_file.write_text("remote-secret", encoding="utf-8")

    called = False

    def fake_get(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("network must not be attempted")

    monkeypatch.setattr(httpx, "get", fake_get)

    rc = data_cmd.cmd_federation_pull(
        argparse.Namespace(
            source="http://peer.example",
            key=str(key),
            from_node="peer-a",
            token_file=str(token_file),
            timeout=30.0,
        )
    )

    assert rc == 1
    assert called is False
