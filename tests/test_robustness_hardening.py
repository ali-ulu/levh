from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from server.connectors.local_files import LocalFilesConnector
from server.core.memory_engine import MemoryEngine
from server.core.trust import recency_score


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "config, message",
    [
        ({"chunk_size": 0}, "chunk_size"),
        ({"chunk_size": 100, "overlap": 100}, "overlap"),
        ({"chunk_size": 100, "overlap": -1}, "overlap"),
    ],
)
async def test_local_connector_rejects_non_progressing_chunk_config(tmp_path, config, message):
    connector = LocalFilesConnector()
    with pytest.raises(ValueError, match=message):
        await connector.connect({"directory": str(tmp_path), **config})


@pytest.mark.asyncio
async def test_local_connector_skips_symlink_file_outside_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("must not be imported", encoding="utf-8")
    (root / "safe.txt").write_text("safe content", encoding="utf-8")
    try:
        (root / "escape.txt").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")

    connector = LocalFilesConnector()
    await connector.connect({"directory": str(root)})
    items = await connector.fetch()
    contents = [item["content"] for item in items]
    assert "safe content" in contents
    assert "must not be imported" not in contents


@pytest.mark.asyncio
async def test_chunker_always_advances(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "large.txt").write_text("x" * 1000, encoding="utf-8")
    connector = LocalFilesConnector()
    await connector.connect({"directory": str(root), "chunk_size": 100, "overlap": 99})
    items = await connector.fetch()
    assert 1 < len(items) <= 1000
    assert all(item["content"] for item in items)


def test_trust_recency_accepts_naive_legacy_timestamp():
    class Legacy:
        created_at = "2026-01-01T12:00:00"

    score = recency_score(Legacy(), datetime.now(timezone.utc).isoformat())
    assert 0.0 <= score <= 1.0


@pytest.mark.asyncio
async def test_new_memory_records_embedding_provenance(tmp_path):
    engine = MemoryEngine(db_path=str(tmp_path / "memory.db"), embedder_mode="hash")
    await engine.initialize()
    try:
        memory = await engine.store("Embedding provenance test", memory_type="episodic")
        receipt = memory.metadata["embedding_provenance"]
        assert receipt == {
            "provider": "hash",
            "model": "stackmemory-hash-v1",
            "dimension": 384,
            "version": "embedding-provenance-v1",
            "requested_mode": "hash",
        }
        assert await engine.db.embedding_dimension_counts() == {384: 1}
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_gated_import_reembeds_and_replaces_provenance(tmp_path):
    engine = MemoryEngine(db_path=str(tmp_path / "import.db"), embedder_mode="hash")
    await engine.initialize()
    try:
        result = await engine.import_memories_gated([
            {
                "content": "Imported external record with enough detail",
                "memory_type": "episodic",
                "embedding": [1.0, 2.0],
                "metadata": {"embedding_provenance": {"provider": "untrusted"}},
            }
        ])
        assert result["imported"] == 1
        memory = (await engine.list_memories(limit=1))[0]
        assert len(memory.embedding or []) == 384
        assert memory.metadata["embedding_provenance"]["provider"] == "hash"
    finally:
        await engine.shutdown()
