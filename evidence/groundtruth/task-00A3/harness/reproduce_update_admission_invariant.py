"""TASK-00A3 characterization: update admission and secret-redaction bypass.

All credentials are synthetic, deliberately non-functional strings.  The
tests exercise the production engine, REST PUT, and real MCP stdio update
paths, then inspect embedding input, SQLite, FTS, recall, and secret audit.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any

import httpx
from httpx import ASGITransport, AsyncClient
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest

from server.core.embedder import Embedder
from server.core.memory_engine import MemoryEngine


def _find_repo_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "pyproject.toml").is_file() and (candidate / "server").is_dir():
            return candidate
    raise RuntimeError("repository root not found from audit harness path")


ROOT = _find_repo_root()
EVIDENCE = Path(
    os.environ.get(
        "LEVH_GROUNDTRUTH_EVIDENCE_DIR",
        ROOT / "evidence" / "groundtruth" / "task-00A3",
    )
)
PROJECT = "GT00A3_UPDATE_ADMISSION"


def _ensure_evidence() -> None:
    (EVIDENCE / "stdout").mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "stderr").mkdir(parents=True, exist_ok=True)


def _reset_evidence() -> None:
    _ensure_evidence()
    for relative in (
        "scenarios.jsonl",
        "embedding-inputs.jsonl",
        "persistence-state.jsonl",
        "surface-inventory.json",
        "stdout/mcp-tool-results.jsonl",
        "stdout/pytest.txt",
        "stderr/mcp.log",
        "stderr/pytest.txt",
    ):
        (EVIDENCE / relative).write_text("", encoding="utf-8")


def _append_jsonl(relative: str, record: dict[str, Any]) -> None:
    _ensure_evidence()
    with (EVIDENCE / relative).open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _write_json(relative: str, record: dict[str, Any]) -> None:
    _ensure_evidence()
    (EVIDENCE / relative).write_text(
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _record(
    *,
    scenario: str,
    surface: str,
    expected: str,
    observed: Any,
    passed: bool,
) -> bool:
    _append_jsonl(
        "scenarios.jsonl",
        {
            "scenario": scenario,
            "surface": surface,
            "expected": expected,
            "observed": observed,
            "passed": passed,
        },
    )
    return passed


def _sqlite_memory(db_path: Path, memory_id: str) -> dict[str, Any] | None:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT id, content, embedding, metadata FROM memories WHERE id = ?",
            (memory_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "content": row["content"],
        "embedding": json.loads(row["embedding"]),
        "metadata": json.loads(row["metadata"]),
    }


def _sqlite_fts(db_path: Path, needle: str) -> list[dict[str, str]]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT memory_id, content FROM memories_fts WHERE memories_fts MATCH ?",
            (needle,),
        ).fetchall()
    return [{"memory_id": row[0], "content": row[1]} for row in rows]


def _tool_text(result: Any) -> str:
    return "\n".join(getattr(item, "text", "") for item in result.content)


async def _capture_embedding_inputs(
    engine: MemoryEngine, monkeypatch: pytest.MonkeyPatch, label: str
) -> list[str]:
    embedder = engine.embedder
    original = embedder.embed
    inputs: list[str] = []

    async def capture(text: str) -> list[float]:
        inputs.append(text)
        _append_jsonl(
            "embedding-inputs.jsonl",
            {"label": label, "input": text, "provider": embedder.identity()["provider"]},
        )
        return await original(text)

    monkeypatch.setattr(embedder, "embed", capture)
    return inputs


@pytest.mark.asyncio
async def test_01_direct_engine_update_persists_and_embeds_raw_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reset_evidence()
    db_path = tmp_path / "direct-engine.db"
    secret_token = "GT00A3SECRETDIRECT7F2A91"
    secret_content = f"api_key={secret_token} synthetic never real"
    engine = MemoryEngine(db_path=str(db_path), embedder_mode="hash")
    await engine.initialize()
    try:
        created = await engine.admit_memory(
            "GT00A3 direct engine safe baseline memory",
            project=PROJECT,
            memory_type="episodic",
        )
        assert created["stored"] is True
        memory_id = created["memory"]["id"]

        decision = await engine.evaluate_admission(secret_content, project=PROJECT)
        decision_pass = decision["action"] == "redact" and secret_token not in decision["redacted_content"]
        _record(
            scenario="direct_preupdate_admission_decision",
            surface="MemoryEngine.evaluate_admission control",
            expected="secret candidate receives redact verdict",
            observed=decision,
            passed=decision_pass,
        )

        inputs = await _capture_embedding_inputs(engine, monkeypatch, "direct_update")
        updated = await engine.update_memory(memory_id, content=secret_content)
        update_pass = updated is not None and updated.content == secret_content
        _record(
            scenario="direct_update_accepts_raw_secret",
            surface="MemoryEngine.update_memory",
            expected="characterize whether raw secret is accepted",
            observed={"content": updated.content if updated else None},
            passed=update_pass,
        )

        embedding_pass = inputs and inputs[0] == secret_content
        _record(
            scenario="direct_update_embedding_input",
            surface="Embedder.embed",
            expected="capture exact content embedded by update",
            observed=inputs,
            passed=embedding_pass,
        )

        persisted = _sqlite_memory(db_path, memory_id)
        expected_embedding = Embedder.hash_embed(secret_content, 384)
        sqlite_pass = (
            persisted is not None
            and persisted["content"] == secret_content
            and persisted["embedding"] == expected_embedding
        )
        _append_jsonl(
            "persistence-state.jsonl",
            {
                "scenario": "direct_update",
                "memory_id": memory_id,
                "sqlite_content": persisted["content"] if persisted else None,
                "embedding_matches_raw_secret": bool(
                    persisted and persisted["embedding"] == expected_embedding
                ),
                "metadata_admission": (
                    persisted["metadata"].get("admission") if persisted else None
                ),
            },
        )
        _record(
            scenario="direct_update_sqlite_raw_secret",
            surface="SQLite memories",
            expected="characterize persisted content and embedding",
            observed={
                "raw_content_present": bool(persisted and secret_token in persisted["content"]),
                "embedding_matches_raw": bool(
                    persisted and persisted["embedding"] == expected_embedding
                ),
            },
            passed=sqlite_pass,
        )

        fts_rows = _sqlite_fts(db_path, secret_token)
        fts_pass = any(row["memory_id"] == memory_id and secret_token in row["content"] for row in fts_rows)
        _record(
            scenario="direct_update_fts_raw_secret",
            surface="SQLite FTS5",
            expected="updated raw secret is searchable",
            observed=fts_rows,
            passed=fts_pass,
        )

        recalled = await engine.recall(secret_content, top_k=10, project=PROJECT, reinforce=False)
        recall_rows = [
            {"id": memory.id, "content": memory.content}
            for memory in recalled.memories
        ]
        recall_pass = any(row["id"] == memory_id and row["content"] == secret_content for row in recall_rows)
        _record(
            scenario="direct_update_recall_raw_secret",
            surface="MemoryEngine.recall",
            expected="updated raw secret is returned by recall",
            observed=recall_rows,
            passed=recall_pass,
        )

        audit = await engine.audit_secrets()
        audit_pass = audit["flagged"] == 1 and audit["items"][0]["id"] == memory_id
        _record(
            scenario="direct_update_secret_audit_flags_bypass",
            surface="MemoryEngine.audit_secrets",
            expected="post-persistence audit detects bypassed secret",
            observed=audit,
            passed=audit_pass,
        )

        assert all(
            [
                decision_pass,
                update_pass,
                embedding_pass,
                sqlite_pass,
                fts_pass,
                recall_pass,
                audit_pass,
            ]
        )
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_02_rest_create_is_gated_but_put_update_bypasses_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import server.api as api_mod
    from server.core import engine_provider

    db_path = tmp_path / "rest.db"
    if api_mod._engine is not None:
        await api_mod._engine.shutdown()
    engine = MemoryEngine(db_path=str(db_path), embedder_mode="hash")
    await engine.initialize()
    api_mod._engine = engine
    api_mod._initialized = True
    engine_provider.set_engine(engine)
    inputs = await _capture_embedding_inputs(engine, monkeypatch, "rest_create_and_update")
    transport = ASGITransport(app=api_mod.app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            create_token = "GT00A3CREATECONTROL38BD10"
            create_secret = f"password={create_token} synthetic create control"
            before = len(inputs)
            create_response = await client.post(
                "/api/memories",
                json={
                    "content": create_secret,
                    "project": PROJECT,
                    "memory_type": "episodic",
                },
            )
            create_inputs = inputs[before:]
            create_body = create_response.json()
            create_pass = (
                create_response.status_code == 200
                and create_token not in create_body["content"]
                and "[REDACTED]" in create_body["content"]
                and create_body["metadata"]["admission"]["action"] == "redact"
            )
            _record(
                scenario="rest_create_redacts_secret_control",
                surface="POST /api/memories",
                expected="create admission redacts before persistence",
                observed={"status": create_response.status_code, "body": create_body},
                passed=create_pass,
            )
            create_embedding_pass = (
                create_inputs
                and all(create_token not in value for value in create_inputs)
                and any("[REDACTED]" in value for value in create_inputs)
            )
            _record(
                scenario="rest_create_embeds_only_redacted_content",
                surface="Embedder.embed via REST create",
                expected="raw create secret never reaches embedder",
                observed=create_inputs,
                passed=create_embedding_pass,
            )

            safe_response = await client.post(
                "/api/memories",
                json={
                    "content": "GT00A3 REST safe baseline before secret update",
                    "project": PROJECT,
                    "memory_type": "episodic",
                },
            )
            assert safe_response.status_code == 200
            update_id = safe_response.json()["id"]
            original_admission = safe_response.json()["metadata"]["admission"]
            rest_token = "GT00A3SECRETRESTC1E487"
            rest_secret = f"token={rest_token} synthetic REST update"
            before = len(inputs)
            update_response = await client.put(
                f"/api/memories/{update_id}", json={"content": rest_secret}
            )
            update_inputs = inputs[before:]
            update_body = update_response.json()
            update_pass = update_response.status_code == 200 and update_body["content"] == rest_secret
            _record(
                scenario="rest_put_accepts_raw_secret",
                surface="PUT /api/memories/{id}",
                expected="characterize whether REST update applies admission/redaction",
                observed={"status": update_response.status_code, "body": update_body},
                passed=update_pass,
            )
            embedding_pass = update_inputs and update_inputs[0] == rest_secret
            _record(
                scenario="rest_put_embeds_raw_secret",
                surface="Embedder.embed via REST PUT",
                expected="capture exact update embedding input",
                observed=update_inputs,
                passed=embedding_pass,
            )

            persisted = _sqlite_memory(db_path, update_id)
            persistence_pass = (
                persisted is not None
                and persisted["content"] == rest_secret
                and persisted["metadata"].get("admission") == original_admission
            )
            _append_jsonl(
                "persistence-state.jsonl",
                {
                    "scenario": "rest_update",
                    "memory_id": update_id,
                    "sqlite_content": persisted["content"] if persisted else None,
                    "metadata_admission_after_update": (
                        persisted["metadata"].get("admission") if persisted else None
                    ),
                    "original_admission_unchanged": bool(
                        persisted
                        and persisted["metadata"].get("admission") == original_admission
                    ),
                },
            )
            _record(
                scenario="rest_put_sqlite_raw_secret_and_stale_admission_receipt",
                surface="SQLite memories",
                expected="characterize content and admission metadata after update",
                observed={
                    "raw_secret_present": bool(persisted and rest_token in persisted["content"]),
                    "original_admission_unchanged": bool(
                        persisted
                        and persisted["metadata"].get("admission") == original_admission
                    ),
                },
                passed=persistence_pass,
            )

            fts_response = await client.get(
                "/api/memories", params={"q": rest_token, "limit": 10}
            )
            fts_body = fts_response.json()
            fts_pass = any(row["id"] == update_id and rest_token in row["content"] for row in fts_body)
            _record(
                scenario="rest_put_fts_exposes_raw_secret",
                surface="GET /api/memories?q= via FTS",
                expected="raw updated secret is searchable",
                observed=fts_body,
                passed=fts_pass,
            )

            recall_response = await client.post(
                "/api/memories/recall",
                json={
                    "query": rest_secret,
                    "top_k": 10,
                    "project": PROJECT,
                    "reinforce": False,
                },
            )
            recall_body = recall_response.json()
            recall_pass = any(
                row["id"] == update_id and row["content"] == rest_secret
                for row in recall_body["memories"]
            )
            _record(
                scenario="rest_put_recall_exposes_raw_secret",
                surface="POST /api/memories/recall",
                expected="raw updated secret is recalled",
                observed=recall_body,
                passed=recall_pass,
            )

            audit_response = await client.get("/api/memories/audit-secrets")
            audit_body = audit_response.json()["audit"]
            audit_pass = any(item["id"] == update_id for item in audit_body["items"])
            _record(
                scenario="rest_put_secret_audit_detects_bypass",
                surface="GET /api/memories/audit-secrets",
                expected="audit flags REST-updated secret",
                observed=audit_body,
                passed=audit_pass,
            )

            duplicate_base = "GT00A3 duplicate policy target alpha beta gamma delta epsilon zeta eta theta"
            duplicate_near = duplicate_base + " revised"
            target = await client.post(
                "/api/memories",
                json={"content": duplicate_base, "project": "GT00A3_DUP", "memory_type": "episodic"},
            )
            assert target.status_code == 200
            exact_create = await client.post(
                "/api/memories",
                json={"content": duplicate_base, "project": "GT00A3_DUP", "memory_type": "episodic"},
            )
            exact_create_body = exact_create.json()
            exact_control_pass = (
                exact_create.status_code == 409
                and exact_create_body["detail"]["decision"]["action"] == "reject"
            )
            _record(
                scenario="rest_create_exact_duplicate_blocked_control",
                surface="POST /api/memories",
                expected="exact duplicate create is rejected",
                observed={"status": exact_create.status_code, "body": exact_create_body},
                passed=exact_control_pass,
            )

            near_create = await client.post(
                "/api/memories",
                json={"content": duplicate_near, "project": "GT00A3_DUP", "memory_type": "episodic"},
            )
            near_create_body = near_create.json()
            near_control_pass = (
                near_create.status_code == 409
                and near_create_body["detail"]["decision"]["action"] == "review"
            )
            _record(
                scenario="rest_create_near_duplicate_held_control",
                surface="POST /api/memories",
                expected="near duplicate create is held for review",
                observed={"status": near_create.status_code, "body": near_create_body},
                passed=near_control_pass,
            )

            safe_exact = await client.post(
                "/api/memories",
                json={"content": "Harbor quartz safe exact update source 7281", "project": "GT00A3_DUP", "memory_type": "episodic"},
            )
            safe_near = await client.post(
                "/api/memories",
                json={"content": "\u0100" * 80 + "Z", "project": "GT00A3_DUP", "memory_type": "episodic"},
            )
            assert safe_exact.status_code == 200 and safe_near.status_code == 200
            exact_update = await client.put(
                f"/api/memories/{safe_exact.json()['id']}",
                json={"content": duplicate_base},
            )
            exact_update_pass = exact_update.status_code == 200 and exact_update.json()["content"] == duplicate_base
            _record(
                scenario="rest_update_bypasses_exact_duplicate_reject",
                surface="PUT /api/memories/{id}",
                expected="characterize exact-duplicate update versus create reject",
                observed={"status": exact_update.status_code, "body": exact_update.json()},
                passed=exact_update_pass,
            )

            near_update = await client.put(
                f"/api/memories/{safe_near.json()['id']}",
                json={"content": duplicate_near},
            )
            near_update_pass = near_update.status_code == 200 and near_update.json()["content"] == duplicate_near
            _record(
                scenario="rest_update_bypasses_near_duplicate_review",
                surface="PUT /api/memories/{id}",
                expected="characterize near-duplicate update versus create review hold",
                observed={"status": near_update.status_code, "body": near_update.json()},
                passed=near_update_pass,
            )

            assert all(
                [
                    create_pass,
                    create_embedding_pass,
                    update_pass,
                    embedding_pass,
                    persistence_pass,
                    fts_pass,
                    recall_pass,
                    audit_pass,
                    exact_control_pass,
                    near_control_pass,
                    exact_update_pass,
                    near_update_pass,
                ]
            )
    finally:
        await engine.shutdown()
        api_mod._engine = None
        api_mod._initialized = False
        engine_provider.set_engine(None)


@pytest.mark.asyncio
async def test_03_real_mcp_stdio_update_bypasses_redaction_and_persists(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "mcp-stdio.db"
    secret_token = "GT00A3SECRETMCP86AD02"
    secret_content = f"client_secret={secret_token} synthetic MCP update"
    seed = MemoryEngine(db_path=str(db_path), embedder_mode="hash")
    await seed.initialize()
    try:
        created = await seed.admit_memory(
            "GT00A3 MCP stdio safe baseline before update",
            project=PROJECT,
            memory_type="episodic",
        )
        memory_id = created["memory"]["id"]
        decision = await seed.evaluate_admission(secret_content, project=PROJECT)
        assert decision["action"] == "redact"
    finally:
        await seed.shutdown()

    env = dict(os.environ)
    env.update(
        {
            "SQLITE_DB_PATH": str(db_path),
            "EMBEDDER_MODE": "hash",
            "LEVH_MCP_PROFILE": "full",
            "PYTHONPATH": str(ROOT),
        }
    )
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "server.mcp_stdio"],
        env=env,
        cwd=str(ROOT),
    )
    with (EVIDENCE / "stderr" / "mcp.log").open("w", encoding="utf-8") as errlog:
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                tool_names = [tool.name for tool in tools.tools]
                assert "update_memory" in tool_names
                result = await session.call_tool(
                    "update_memory",
                    {"memory_id": memory_id, "content": secret_content},
                )
                result_text = _tool_text(result)
                _append_jsonl(
                    "stdout/mcp-tool-results.jsonl",
                    {
                        "tool": "update_memory",
                        "memory_id": memory_id,
                        "is_error": result.isError,
                        "text": result_text,
                    },
                )
                tool_pass = result.isError is False and secret_token in result_text
                _record(
                    scenario="mcp_stdio_update_accepts_and_echoes_raw_secret",
                    surface="MCP stdio update_memory",
                    expected="characterize MCP update output and policy",
                    observed={"is_error": result.isError, "text": result_text},
                    passed=tool_pass,
                )

    persisted = _sqlite_memory(db_path, memory_id)
    expected_embedding = Embedder.hash_embed(secret_content, 384)
    sqlite_pass = (
        persisted is not None
        and persisted["content"] == secret_content
        and persisted["embedding"] == expected_embedding
    )
    _append_jsonl(
        "persistence-state.jsonl",
        {
            "scenario": "mcp_stdio_update",
            "memory_id": memory_id,
            "sqlite_content": persisted["content"] if persisted else None,
            "embedding_matches_raw_secret": bool(
                persisted and persisted["embedding"] == expected_embedding
            ),
            "preupdate_admission_action": decision["action"],
        },
    )
    _record(
        scenario="mcp_stdio_update_sqlite_raw_secret",
        surface="SQLite memories",
        expected="raw MCP-updated secret and its embedding persist",
        observed={
            "content": persisted["content"] if persisted else None,
            "embedding_matches_raw_secret": bool(
                persisted and persisted["embedding"] == expected_embedding
            ),
        },
        passed=sqlite_pass,
    )
    fts_rows = _sqlite_fts(db_path, secret_token)
    fts_pass = any(row["memory_id"] == memory_id and secret_token in row["content"] for row in fts_rows)
    _record(
        scenario="mcp_stdio_update_fts_raw_secret",
        surface="SQLite FTS5",
        expected="raw MCP-updated secret is searchable",
        observed=fts_rows,
        passed=fts_pass,
    )

    verifier = MemoryEngine(db_path=str(db_path), embedder_mode="hash")
    await verifier.initialize()
    try:
        recalled = await verifier.recall(secret_content, top_k=10, project=PROJECT, reinforce=False)
        recall_rows = [{"id": memory.id, "content": memory.content} for memory in recalled.memories]
        recall_pass = any(row["id"] == memory_id and row["content"] == secret_content for row in recall_rows)
        _record(
            scenario="mcp_stdio_update_recall_raw_secret",
            surface="MemoryEngine recall after MCP process exit",
            expected="raw MCP-updated secret is recalled from persisted state",
            observed=recall_rows,
            passed=recall_pass,
        )
        audit = await verifier.audit_secrets()
        audit_pass = any(item["id"] == memory_id for item in audit["items"])
        _record(
            scenario="mcp_stdio_update_secret_audit_detects_bypass",
            surface="MemoryEngine.audit_secrets",
            expected="audit flags MCP-updated secret",
            observed=audit,
            passed=audit_pass,
        )
    finally:
        await verifier.shutdown()

    assert all([tool_pass, sqlite_pass, fts_pass, recall_pass, audit_pass])


def test_04_update_surface_inventory_and_evidence_consistency() -> None:
    import server.api as api_mod

    cli_text = (ROOT / "server" / "cli.py").read_text(encoding="utf-8")
    websocket_source = inspect.getsource(api_mod.memory_websocket)
    rest_source = inspect.getsource(api_mod.update_memory)
    mcp_source = (ROOT / "server" / "tools" / "update.py").read_text(encoding="utf-8")
    inventory = {
        "direct_engine_update": True,
        "rest_update": "engine.update_memory" in rest_source,
        "mcp_update": "engine.update_memory" in mcp_source,
        "cli_update": "update_memory" in cli_text,
        "websocket_update": 'action == "update"' in websocket_source,
        "websocket_actions": ["store", "recall", "forget", "stats", "ping"],
    }
    _write_json("surface-inventory.json", inventory)
    inventory_pass = (
        inventory["direct_engine_update"]
        and inventory["rest_update"]
        and inventory["mcp_update"]
        and not inventory["cli_update"]
        and not inventory["websocket_update"]
    )
    _record(
        scenario="content_update_surface_inventory",
        surface="source-backed surface inventory",
        expected="engine, REST and MCP update exist; CLI/WebSocket update absent",
        observed=inventory,
        passed=inventory_pass,
    )

    scenario_rows = [
        json.loads(line)
        for line in (EVIDENCE / "scenarios.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert inventory_pass
    assert len(scenario_rows) == 25
    assert all(row["passed"] for row in scenario_rows)
