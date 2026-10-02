"""Tests for the offline signed federation envelope (#338).

Offline and deterministic: no transport, no model key, no dataset. The
ed25519 cases skip cleanly when ``cryptography`` is absent, matching how the
rest of the suite treats that optional dependency.
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["EMBEDDER_MODE"] = "hash"

from server.core.federation import (
    EnvelopeError,
    sign_envelope,
    verify_envelope,
)


def _run_cli(*args: str, db_path: str, timeout: int = 120):
    return subprocess.run(
        [sys.executable, "-m", "server.cli", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "SQLITE_DB_PATH": db_path, "EMBEDDER_MODE": "hash"},
    )


def _seed(db_path: str, content: str) -> None:
    import asyncio

    from server.core.memory_engine import MemoryEngine

    async def _run() -> None:
        engine = MemoryEngine(db_path=db_path, embedder_mode="hash")
        await engine.initialize()
        await engine.store(content, memory_type="episodic")
        await engine.shutdown()

    asyncio.run(_run())


def _bundle() -> dict:
    return {
        "format": "levh-full-export",
        "version": 1,
        "created_at": "2026-10-02T00:00:00+00:00",
        "counts": {"memories": 2, "entities": 0, "trust_scores": 0, "conflicts": 0},
        "memories": [
            {"id": "m1", "content": "Atlas uses PostgreSQL in production", "metadata": {}},
            {"id": "m2", "content": "The release is on Friday", "metadata": {}},
        ],
        "entities": [],
        "entity_stats": {"by_type": {}},
        "trust": [],
        "conflicts": [],
    }


def _write_key(text: str) -> str:
    fd, path = tempfile.mkstemp()
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def test_hmac_envelope_round_trips():
    key = _write_key("shared-secret")
    try:
        envelope = sign_envelope(_bundle(), node_id="laptop", key_path=key)
        assert envelope["format"] == "levh-federation-envelope"
        assert envelope["node_id"] == "laptop"
        assert envelope["algorithm"] == "hmac-sha256"
        # The envelope must survive a JSON round-trip before verification, or a
        # receiver could never check what the sender signed.
        transported = json.loads(json.dumps(envelope))
        bundle = verify_envelope(transported, key_path=key)
        assert bundle["counts"]["memories"] == 2
        assert bundle["memories"][0]["content"] == "Atlas uses PostgreSQL in production"
    finally:
        os.unlink(key)


def test_tampered_bundle_is_rejected_loudly():
    key = _write_key("shared-secret")
    try:
        envelope = sign_envelope(_bundle(), node_id="laptop", key_path=key)
        envelope["bundle"]["memories"].append({"id": "m3", "content": "injected"})
        with pytest.raises(EnvelopeError):
            verify_envelope(envelope, key_path=key)
    finally:
        os.unlink(key)


def test_wrong_key_is_rejected():
    key = _write_key("shared-secret")
    other = _write_key("a-different-secret")
    try:
        envelope = sign_envelope(_bundle(), node_id="laptop", key_path=key)
        with pytest.raises(EnvelopeError):
            verify_envelope(envelope, key_path=other)
    finally:
        os.unlink(key)
        os.unlink(other)


def test_expected_node_id_mismatch_is_rejected():
    key = _write_key("shared-secret")
    try:
        envelope = sign_envelope(_bundle(), node_id="laptop", key_path=key)
        with pytest.raises(EnvelopeError):
            verify_envelope(envelope, key_path=key, expected_node_id="workstation")
    finally:
        os.unlink(key)


def test_missing_signature_is_rejected():
    key = _write_key("shared-secret")
    try:
        envelope = sign_envelope(_bundle(), node_id="laptop", key_path=key)
        del envelope["signature"]
        with pytest.raises(EnvelopeError):
            verify_envelope(envelope, key_path=key)
    finally:
        os.unlink(key)


def test_non_envelope_input_is_rejected():
    key = _write_key("shared-secret")
    try:
        with pytest.raises(EnvelopeError):
            verify_envelope({"format": "levh-full-export"}, key_path=key)
    finally:
        os.unlink(key)


def _ed25519_keypair():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.generate()
    priv_pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    pub_pem = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    return priv_pem, pub_pem


def test_ed25519_envelope_verifies_with_public_key_only():
    pytest.importorskip("cryptography")
    priv_pem, pub_pem = _ed25519_keypair()
    priv = _write_key(priv_pem)
    pub = _write_key(pub_pem)
    try:
        envelope = sign_envelope(
            _bundle(), node_id="workstation", key_path=priv, algorithm="ed25519"
        )
        assert envelope["algorithm"] == "ed25519"
        transported = json.loads(json.dumps(envelope))
        bundle = verify_envelope(transported, key_path=pub)
        assert bundle["counts"]["memories"] == 2
    finally:
        os.unlink(priv)
        os.unlink(pub)


def test_ed25519_tamper_is_rejected():
    pytest.importorskip("cryptography")
    priv_pem, pub_pem = _ed25519_keypair()
    priv = _write_key(priv_pem)
    pub = _write_key(pub_pem)
    try:
        envelope = sign_envelope(
            _bundle(), node_id="workstation", key_path=priv, algorithm="ed25519"
        )
        envelope["node_id"] = "impostor"
        with pytest.raises(EnvelopeError):
            verify_envelope(envelope, key_path=pub)
    finally:
        os.unlink(priv)
        os.unlink(pub)


def test_ed25519_verification_accepts_the_sender_private_key():
    """A receiver holding the sender's own key file must verify too.

    ``_load_ed25519_public`` falls back to deriving the public half from a
    private PEM so the same file the sender signed with can verify. Returning
    the bound ``public_key`` method instead of calling it raised
    ``AttributeError`` here rather than verifying.
    """
    pytest.importorskip("cryptography")
    priv_pem, _ = _ed25519_keypair()
    priv = _write_key(priv_pem)
    try:
        envelope = sign_envelope(
            _bundle(), node_id="workstation", key_path=priv, algorithm="ed25519"
        )
        bundle = verify_envelope(envelope, key_path=priv)
        assert bundle["counts"]["memories"] == 2
    finally:
        os.unlink(priv)


class TestFederationCli:
    """The CLI pair is what an operator actually runs; exercise it end to end.

    The envelope is only trustworthy if the wiring is: export must sign the
    real engine bundle, import must verify before it writes anything, and a
    tampered envelope must exit non-zero without touching the store.
    """

    def _engine(self, tmp_path, name):
        from server.core.memory_engine import MemoryEngine

        eng = MemoryEngine(
            db_path=str(tmp_path / name), embedder_mode="hash", short_term_max=10
        )
        asyncio.run(eng.initialize())
        return eng

    def _stored(self, tmp_path, name):
        from server.core.memory_engine import MemoryEngine

        eng = MemoryEngine(
            db_path=str(tmp_path / name), embedder_mode="hash", short_term_max=10
        )

        async def _read():
            await eng.initialize()
            return await eng.list_memories(limit=50)

        try:
            return asyncio.run(_read())
        finally:
            asyncio.run(eng.shutdown())

    def _export(self, tmp_path):
        from server.core import engine_provider
        from server.commands import data as data_cmd

        key = tmp_path / "shared.key"
        key.write_text("operator-shared-secret", encoding="utf-8")
        envelope = tmp_path / "bundle.json"
        engine_provider.set_engine(self._engine(tmp_path, "src.db"))
        try:
            rc = data_cmd.cmd_federation_export(
                argparse.Namespace(
                    node_id="workstation",
                    key=str(key),
                    algorithm="hmac-sha256",
                    out=str(envelope),
                )
            )
        finally:
            engine_provider.set_engine(None)
        return rc, envelope, key

    def _import(self, tmp_path, envelope, key, from_node=""):
        from server.core import engine_provider
        from server.commands import data as data_cmd

        engine_provider.set_engine(self._engine(tmp_path, "dst.db"))
        try:
            return data_cmd.cmd_federation_import(
                argparse.Namespace(
                    envelope=str(envelope), key=str(key), from_node=from_node
                )
            )
        finally:
            engine_provider.set_engine(None)

    def _seed_src(self, tmp_path, content):
        src = self._engine(tmp_path, "src.db")
        try:
            asyncio.run(src.store(content, memory_type="episodic"))
        finally:
            asyncio.run(src.shutdown())

    def test_export_then_import_round_trips(self, tmp_path):
        self._seed_src(tmp_path, "the build server listens on port 8930")
        rc, envelope, key = self._export(tmp_path)
        assert rc == 0
        assert json.load(open(envelope, encoding="utf-8"))["node_id"] == "workstation"

        assert self._import(tmp_path, envelope, key) == 0
        contents = [m.content for m in self._stored(tmp_path, "dst.db")]
        assert any("port 8930" in c for c in contents), contents

    def test_tampered_envelope_is_rejected_loudly_and_imports_nothing(self, tmp_path):
        self._seed_src(tmp_path, "a peer memory that will be tampered with")
        rc, envelope, key = self._export(tmp_path)
        assert rc == 0

        tampered = json.load(open(envelope, encoding="utf-8"))
        tampered["bundle"]["memories"][0]["content"] = "a forged memory"
        with open(envelope, "w", encoding="utf-8") as f:
            json.dump(tampered, f)

        assert self._import(tmp_path, envelope, key) == 1
        assert self._stored(tmp_path, "dst.db") == []

    def test_wrong_key_is_rejected(self, tmp_path):
        self._seed_src(tmp_path, "a peer memory")
        rc, envelope, _key = self._export(tmp_path)
        assert rc == 0
        wrong = tmp_path / "wrong.key"
        wrong.write_text("someone-elses-secret", encoding="utf-8")

        assert self._import(tmp_path, envelope, wrong) == 1
        assert self._stored(tmp_path, "dst.db") == []

    def test_from_node_mismatch_is_rejected(self, tmp_path):
        self._seed_src(tmp_path, "a peer memory")
        rc, envelope, key = self._export(tmp_path)
        assert rc == 0

        assert self._import(tmp_path, envelope, key, from_node="someone-else") == 1

    def test_missing_envelope_file_is_rejected(self, tmp_path):
        from server.core import engine_provider
        from server.commands import data as data_cmd

        key = tmp_path / "shared.key"
        key.write_text("operator-shared-secret", encoding="utf-8")
        engine_provider.set_engine(self._engine(tmp_path, "dst.db"))
        try:
            rc = data_cmd.cmd_federation_import(
                argparse.Namespace(
                    envelope=str(tmp_path / "nope.json"), key=str(key), from_node=""
                )
            )
        finally:
            engine_provider.set_engine(None)
        assert rc == 1

    def test_main_dispatch_reaches_the_pair(self, tmp_path, monkeypatch, capsys):
        """Typing ``levh federation-export`` must reach the command, not just
        exist in the parser — the dispatch chain is what an operator touches."""
        import server.cli as cli
        from server.core import engine_provider

        self._seed_src(tmp_path, "the build server listens on port 8930")
        key = tmp_path / "shared.key"
        key.write_text("operator-shared-secret", encoding="utf-8")
        envelope = tmp_path / "bundle.json"

        engine_provider.set_engine(self._engine(tmp_path, "src.db"))
        try:
            monkeypatch.setattr(
                sys,
                "argv",
                [
                    "levh", "federation-export",
                    "--node-id", "workstation",
                    "--key", str(key),
                    "--out", str(envelope),
                ],
            )
            assert cli.main() == 0
        finally:
            engine_provider.set_engine(None)
        assert "node workstation" in capsys.readouterr().out

        engine_provider.set_engine(self._engine(tmp_path, "dst.db"))
        try:
            monkeypatch.setattr(
                sys, "argv",
                ["levh", "federation-import", str(envelope), "--key", str(key)],
            )
            assert cli.main() == 0
        finally:
            engine_provider.set_engine(None)
        assert "Imported 1 memories through the admission gate" in capsys.readouterr().out

    def test_cli_entrypoint_dispatches_the_pair(self, tmp_path):
        """One subprocess run proves argparse and dispatch reach the commands."""
        src = str(tmp_path / "src.db")
        key = tmp_path / "shared.key"
        key.write_text("operator-shared-secret", encoding="utf-8")
        envelope = str(tmp_path / "bundle.json")
        _seed(src, "the build server listens on port 8930")

        exported = _run_cli(
            "federation-export",
            "--node-id", "workstation",
            "--key", str(key),
            "--out", envelope,
            db_path=src,
        )
        assert exported.returncode == 0, exported.stderr
        assert "node workstation" in exported.stdout

        imported = _run_cli(
            "federation-import", envelope, "--key", str(key), db_path=str(tmp_path / "dst.db")
        )
        assert imported.returncode == 0, imported.stderr
        assert "Imported 1 memories through the admission gate" in imported.stdout
