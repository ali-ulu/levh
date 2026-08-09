"""Regression coverage for issue #16: JSON imports must never truncate."""

from __future__ import annotations

import json

import pytest

from server.connectors.local_files import LocalFilesConnector


def _rows_for(rows: list[dict], file_name: str) -> list[dict]:
    matched = [row for row in rows if row["metadata"]["file_name"] == file_name]
    return sorted(matched, key=lambda row: row["metadata"].get("chunk_index", 0))


@pytest.mark.asyncio
async def test_valid_json_chunking_matches_normal_text_and_preserves_tail(tmp_path):
    data = {
        f"key_{i}": f"value-{i}-{'x' * 80}"
        for i in range(80)
    }
    data["tail"] = "VALID_JSON_TAIL_SENTINEL"

    raw = json.dumps(data, ensure_ascii=False)
    normalized = "\n".join(
        f"{key}: {json.dumps(value, ensure_ascii=False)}"
        for key, value in data.items()
    )
    (tmp_path / "large.json").write_text(raw, encoding="utf-8")
    (tmp_path / "normalized.txt").write_text(normalized, encoding="utf-8")

    connector = LocalFilesConnector()
    await connector.connect(
        {"directory": str(tmp_path), "chunk_size": 250, "overlap": 50}
    )
    rows = await connector.fetch()

    json_rows = _rows_for(rows, "large.json")
    text_rows = _rows_for(rows, "normalized.txt")

    assert len(json_rows) > 1
    assert [row["content"] for row in json_rows] == [
        row["content"] for row in text_rows
    ]
    assert any("VALID_JSON_TAIL_SENTINEL" in row["content"] for row in json_rows)
    assert all(row["metadata"]["json_keys"] == list(data.keys()) for row in json_rows)


@pytest.mark.asyncio
async def test_invalid_json_chunking_matches_normal_text_and_preserves_tail(tmp_path):
    raw = "{" + ("broken-json-body-" * 300) + "INVALID_JSON_TAIL_SENTINEL"
    (tmp_path / "broken.json").write_text(raw, encoding="utf-8")
    (tmp_path / "broken.txt").write_text(raw, encoding="utf-8")

    connector = LocalFilesConnector()
    await connector.connect(
        {"directory": str(tmp_path), "chunk_size": 220, "overlap": 40}
    )
    rows = await connector.fetch()

    json_rows = _rows_for(rows, "broken.json")
    text_rows = _rows_for(rows, "broken.txt")

    assert len(json_rows) > 1
    assert [row["content"] for row in json_rows] == [
        row["content"] for row in text_rows
    ]
    assert any("INVALID_JSON_TAIL_SENTINEL" in row["content"] for row in json_rows)
    assert all("json-parse-error" in row["tags"] for row in json_rows)


@pytest.mark.asyncio
async def test_json_without_chunk_size_stays_single_memory_without_truncation(tmp_path):
    valid_data = {
        "body": "v" * 5000,
        "tail": "UNCHUNKED_VALID_TAIL_SENTINEL",
    }
    valid_raw = json.dumps(valid_data, ensure_ascii=False)
    invalid_raw = "{" + ("z" * 5000) + "UNCHUNKED_INVALID_TAIL_SENTINEL"
    (tmp_path / "valid.json").write_text(valid_raw, encoding="utf-8")
    (tmp_path / "invalid.json").write_text(invalid_raw, encoding="utf-8")

    connector = LocalFilesConnector()
    await connector.connect({"directory": str(tmp_path)})
    rows = await connector.fetch()

    valid_rows = _rows_for(rows, "valid.json")
    invalid_rows = _rows_for(rows, "invalid.json")

    assert len(valid_rows) == 1
    assert len(invalid_rows) == 1
    assert "UNCHUNKED_VALID_TAIL_SENTINEL" in valid_rows[0]["content"]
    assert invalid_rows[0]["content"] == invalid_raw
    assert "UNCHUNKED_INVALID_TAIL_SENTINEL" in invalid_rows[0]["content"]
