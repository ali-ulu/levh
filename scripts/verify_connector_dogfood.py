"""Verify real Git/GitHub dogfood ingestion and emit machine-readable evidence."""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
from collections import Counter
from pathlib import Path

from server.core.memory_engine import MemoryEngine
from server.core.trust import source_type


def _rows(conn: sqlite3.Connection, sql: str, params=()) -> list[dict]:
    cur = conn.execute(sql, params)
    names = [item[0] for item in cur.description]
    return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def _memory_inventory(conn: sqlite3.Connection, source: str) -> dict:
    rows = _rows(
        conn,
        """
        SELECT id, source, content, metadata_json
          FROM memories
         WHERE source = ?
         ORDER BY created_at ASC, rowid ASC
        """,
        (source,),
    )
    types: Counter[str] = Counter()
    for row in rows:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            metadata = {}
        types[str(metadata.get("type") or "unknown")] += 1
    return {
        "count": len(rows),
        "types": dict(sorted(types.items())),
        "sample_id": rows[0]["id"] if rows else None,
        "sample_content": rows[0]["content"] if rows else "",
    }


async def _recall_probe(
    db_path: str,
    *,
    source: str,
    sample_id: str,
    sample_content: str,
    project: str,
) -> dict:
    words = [word.strip(".,:;()[]{}<>").lower() for word in sample_content.split()]
    query = " ".join(word for word in words if len(word) >= 4)[:240]
    if not query:
        query = sample_content[:240]

    engine = MemoryEngine(db_path=db_path, embedder_mode="hash", short_term_max=50)
    await engine.initialize()
    try:
        result = await engine.recall(
            query,
            top_k=20,
            project=project,
            reinforce=False,
        )
        ids = [memory.id for memory in result.memories]
        return {
            "source": source,
            "query_chars": len(query),
            "sample_id": sample_id,
            "hit": sample_id in ids,
            "rank": ids.index(sample_id) + 1 if sample_id in ids else None,
            "returned": len(ids),
        }
    finally:
        await engine.shutdown()


async def _build_report(db_path: str, project: str) -> dict:
    conn = sqlite3.connect(db_path)
    try:
        sync_rows = _rows(
            conn,
            """
            SELECT connector, project, last_synced_at, last_fetched, last_stored,
                   total_stored, runs
              FROM connector_sync
             WHERE connector IN ('git', 'github')
             ORDER BY connector
            """,
        )
        git = _memory_inventory(conn, "connector:git")
        github = _memory_inventory(conn, "connector:github")
    finally:
        conn.close()

    sync_by_connector = {row["connector"]: row for row in sync_rows}
    errors: list[str] = []
    for name in ("git", "github"):
        row = sync_by_connector.get(name)
        if row is None:
            errors.append(f"missing connector_sync row for {name}")
        elif int(row.get("last_fetched") or 0) <= 0:
            errors.append(f"{name} fetched no items")

    if git["count"] <= 0:
        errors.append("git stored no memories")
    if github["count"] <= 0:
        errors.append("github stored no memories")
    if git["types"].get("commit", 0) <= 0:
        errors.append("git produced no commit memories")
    if not any(github["types"].get(kind, 0) > 0 for kind in ("readme", "file", "issue", "pull_request")):
        errors.append("github produced no expected memory types")

    probes = []
    for source, inventory in (
        ("connector:git", git),
        ("connector:github", github),
    ):
        if inventory["sample_id"]:
            probes.append(
                await _recall_probe(
                    db_path,
                    source=source,
                    sample_id=inventory["sample_id"],
                    sample_content=inventory["sample_content"],
                    project=project,
                )
            )

    for probe in probes:
        if not probe["hit"]:
            errors.append(f"{probe['source']} sample was not recallable")

    for inventory in (git, github):
        inventory.pop("sample_content", None)

    return {
        "ok": not errors,
        "project": project,
        "sync": sync_rows,
        "memories": {
            "git": {
                **git,
                "provenance_type": source_type("connector:git"),
            },
            "github": {
                **github,
                "provenance_type": source_type("connector:github"),
            },
        },
        "recall_probes": probes,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--project", default="levh")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    report = asyncio.run(_build_report(args.db, args.project))
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
