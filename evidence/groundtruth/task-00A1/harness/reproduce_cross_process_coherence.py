"""Ground-truth reproduction for P0-1 cross-process context coherence.

This file is intentionally a characterization test, not a remediation.  It
keeps two independent MemoryEngine processes (and, separately, real REST and
MCP stdio transports) alive against the same SQLite database and records what
each observer can see without a restart.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import sys
from typing import Any, AsyncIterator

import httpx
import pytest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _find_repo_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "pyproject.toml").is_file() and (candidate / "server").is_dir():
            return candidate
    raise RuntimeError("repository root not found from audit harness path")


ROOT = _find_repo_root()
EVIDENCE = Path(
    os.environ.get(
        "LEVH_GROUNDTRUTH_EVIDENCE_DIR",
        ROOT / "evidence" / "groundtruth" / "task-00A1",
    )
)
PROJECT = "GT00A1_CROSS_PROCESS"


def _ensure_evidence() -> None:
    (EVIDENCE / "stdout").mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "stderr").mkdir(parents=True, exist_ok=True)


def _append_jsonl(name: str, record: dict[str, Any]) -> None:
    _ensure_evidence()
    with (EVIDENCE / name).open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _append_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _reset_files(*relative_paths: str) -> None:
    _ensure_evidence()
    for relative in relative_paths:
        path = EVIDENCE / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")


def _memory_payload(memory: Any) -> dict[str, Any] | None:
    if memory is None:
        return None
    return memory.model_dump(mode="json", exclude={"embedding"})


def _recall_payload(result: Any) -> list[dict[str, Any]]:
    return [
        {
            "id": memory.id,
            "content": memory.content,
            "score": score,
        }
        for memory, score in zip(result.memories, result.scores)
    ]


async def _worker_main(db_path: str, label: str) -> None:
    from server.core.memory_engine import MemoryEngine

    engine = MemoryEngine(db_path=db_path, embedder_mode="hash")
    await engine.initialize()
    print(json.dumps({"ready": True, "label": label, "pid": os.getpid()}), flush=True)
    try:
        while True:
            line = await asyncio.to_thread(sys.stdin.readline)
            if not line:
                break
            request = json.loads(line)
            operation = request["operation"]
            try:
                if operation == "store":
                    memory = await engine.store(
                        content=request["content"],
                        importance=0.8,
                        tags=["groundtruth", "00A1"],
                        project=PROJECT,
                        memory_type="episodic",
                    )
                    result: Any = _memory_payload(memory)
                elif operation == "get":
                    result = _memory_payload(await engine.get_memory(request["memory_id"]))
                elif operation == "recall":
                    recalled = await engine.recall(
                        query=request["query"],
                        top_k=10,
                        project=PROJECT,
                        reinforce=False,
                    )
                    result = _recall_payload(recalled)
                elif operation == "update":
                    result = _memory_payload(
                        await engine.update_memory(
                            request["memory_id"], content=request["content"]
                        )
                    )
                elif operation == "forget":
                    result = await engine.forget(request["memory_id"])
                elif operation == "shutdown":
                    print(json.dumps({"ok": True, "result": "shutdown"}), flush=True)
                    break
                else:
                    raise ValueError(f"unknown operation: {operation}")
                print(json.dumps({"ok": True, "result": result}), flush=True)
            except Exception as exc:  # evidence protocol must report worker failures
                print(
                    json.dumps(
                        {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                    ),
                    flush=True,
                )
    finally:
        await engine.shutdown()


class EngineWorker:
    def __init__(self, db_path: Path, label: str):
        self.db_path = db_path
        self.label = label
        self.process: asyncio.subprocess.Process | None = None
        self._stderr = None

    async def start(self) -> None:
        _ensure_evidence()
        stderr_path = EVIDENCE / "stderr" / f"engine-{self.label}.log"
        self._stderr = stderr_path.open("ab")
        env = dict(os.environ)
        env.update({"PYTHONPATH": str(ROOT), "EMBEDDER_MODE": "hash"})
        self.process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-u",
            str(Path(__file__).resolve()),
            "--engine-worker",
            str(self.db_path),
            self.label,
            cwd=str(ROOT),
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=self._stderr,
        )
        line = await asyncio.wait_for(self.process.stdout.readline(), timeout=20)
        if not line:
            raise AssertionError(f"engine worker {self.label} exited before ready")
        _append_text(
            EVIDENCE / "stdout" / f"engine-{self.label}.jsonl",
            line.decode("utf-8"),
        )
        ready = json.loads(line)
        assert ready["ready"] is True
        _append_text(
            EVIDENCE / "process-map.txt",
            f"engine {self.label}: pid={ready['pid']} db={self.db_path}\n",
        )

    async def request(self, operation: str, **payload: Any) -> Any:
        assert self.process is not None
        assert self.process.stdin is not None
        assert self.process.stdout is not None
        message = {"operation": operation, **payload}
        self.process.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        await self.process.stdin.drain()
        line = await asyncio.wait_for(self.process.stdout.readline(), timeout=20)
        if not line:
            raise AssertionError(f"engine worker {self.label} closed its protocol")
        _append_text(
            EVIDENCE / "stdout" / f"engine-{self.label}.jsonl",
            line.decode("utf-8"),
        )
        response = json.loads(line)
        if not response.get("ok"):
            raise AssertionError(f"worker {self.label}: {response.get('error')}")
        return response["result"]

    async def stop(self) -> None:
        if self.process is not None and self.process.returncode is None:
            try:
                await self.request("shutdown")
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except (AssertionError, asyncio.TimeoutError):
                self.process.kill()
                await self.process.wait()
        if self._stderr is not None:
            self._stderr.close()
        self.process = None


async def _seed(db_path: Path, contents: list[str]) -> list[dict[str, Any]]:
    from server.core.memory_engine import MemoryEngine

    engine = MemoryEngine(db_path=str(db_path), embedder_mode="hash")
    await engine.initialize()
    seeded: list[dict[str, Any]] = []
    try:
        for content in contents:
            memory = await engine.store(
                content=content,
                importance=0.8,
                tags=["groundtruth", "00A1", "seed"],
                project=PROJECT,
                memory_type="episodic",
            )
            seeded.append(_memory_payload(memory))
    finally:
        await engine.shutdown()
    return seeded


def _contains(rows: list[dict[str, Any]], memory_id: str, content: str | None = None) -> bool:
    return any(
        row.get("id") == memory_id
        and (content is None or row.get("content") == content)
        for row in rows
    )


def _record(
    file_name: str,
    *,
    scenario: str,
    direction: str,
    operation: str,
    observer_state: str,
    expected: str,
    observed: Any,
    passed: bool,
) -> bool:
    _append_jsonl(
        file_name,
        {
            "scenario": scenario,
            "direction": direction,
            "operation": operation,
            "observer_state": observer_state,
            "expected": expected,
            "observed": observed,
            "passed": passed,
        },
    )
    return passed


def _sqlite_snapshot(db_path: Path, section: str) -> None:
    with sqlite3.connect(db_path) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        rows = connection.execute(
            "SELECT id, content, project FROM memories ORDER BY created_at, id"
        ).fetchall()
    lines = [
        f"[{section}]",
        f"database={db_path}",
        f"integrity_check={integrity}",
        f"memory_count={len(rows)}",
    ]
    lines.extend(f"{memory_id}\t{project}\t{content}" for memory_id, content, project in rows)
    _append_text(EVIDENCE / "sqlite-state.txt", "\n".join(lines) + "\n\n")


@pytest.mark.asyncio
async def test_engine_cross_process_coherence(tmp_path: Path) -> None:
    _reset_files(
        "engine-scenarios.jsonl",
        "process-map.txt",
        "sqlite-state.txt",
        "stdout/engine-A.jsonl",
        "stdout/engine-B.jsonl",
        "stdout/engine-A-restarted.jsonl",
        "stdout/engine-B-restarted.jsonl",
        "stderr/engine-A.log",
        "stderr/engine-B.log",
        "stderr/engine-A-restarted.log",
        "stderr/engine-B-restarted.log",
    )
    db_path = tmp_path / "engine-coherence.db"
    tokens = {
        "update_a_old": "GT00A1_ENGINE_UPDATE_A_OLD_49D7F2 exact baseline",
        "update_a_new": "GT00A1_ENGINE_UPDATE_A_NEW_936C1B exact replacement",
        "update_b_old": "GT00A1_ENGINE_UPDATE_B_OLD_C284E5 exact baseline",
        "update_b_new": "GT00A1_ENGINE_UPDATE_B_NEW_31AF90 exact replacement",
        "delete_a": "GT00A1_ENGINE_DELETE_A_741DB3 exact ghost probe",
        "delete_b": "GT00A1_ENGINE_DELETE_B_6E05AC exact ghost probe",
        "create_a": "GT00A1_ENGINE_CREATE_A_F1509D exact created memory",
        "create_b": "GT00A1_ENGINE_CREATE_B_A82C46 exact created memory",
    }
    seeded = await _seed(
        db_path,
        [
            tokens["update_a_old"],
            tokens["update_b_old"],
            tokens["delete_a"],
            tokens["delete_b"],
        ],
    )
    update_a, update_b, delete_a, delete_b = seeded
    a = EngineWorker(db_path, "A")
    b = EngineWorker(db_path, "B")
    failures: list[str] = []
    try:
        await a.start()
        await b.start()

        created_a = await a.request("store", content=tokens["create_a"])
        b_get = await b.request("get", memory_id=created_a["id"])
        passed = b_get is not None and b_get["content"] == tokens["create_a"]
        _record(
            "engine-scenarios.jsonl",
            scenario="create_db_visibility",
            direction="A_to_B",
            operation="create",
            observer_state="live_without_restart",
            expected="B get returns A-created memory",
            observed=b_get,
            passed=passed,
        )
        b_recall = await b.request("recall", query=tokens["create_a"])
        passed = _contains(b_recall, created_a["id"], tokens["create_a"])
        if not passed:
            failures.append("A_to_B create recall")
        _record(
            "engine-scenarios.jsonl",
            scenario="create_recall_visibility",
            direction="A_to_B",
            operation="create",
            observer_state="live_without_restart",
            expected="B recall returns A-created memory",
            observed=b_recall,
            passed=passed,
        )

        created_b = await b.request("store", content=tokens["create_b"])
        a_get = await a.request("get", memory_id=created_b["id"])
        passed = a_get is not None and a_get["content"] == tokens["create_b"]
        _record(
            "engine-scenarios.jsonl",
            scenario="create_db_visibility",
            direction="B_to_A",
            operation="create",
            observer_state="live_without_restart",
            expected="A get returns B-created memory",
            observed=a_get,
            passed=passed,
        )
        a_recall = await a.request("recall", query=tokens["create_b"])
        passed = _contains(a_recall, created_b["id"], tokens["create_b"])
        if not passed:
            failures.append("B_to_A create recall")
        _record(
            "engine-scenarios.jsonl",
            scenario="create_recall_visibility",
            direction="B_to_A",
            operation="create",
            observer_state="live_without_restart",
            expected="A recall returns B-created memory",
            observed=a_recall,
            passed=passed,
        )

        await a.request(
            "update", memory_id=update_a["id"], content=tokens["update_a_new"]
        )
        b_get = await b.request("get", memory_id=update_a["id"])
        passed = b_get is not None and b_get["content"] == tokens["update_a_new"]
        _record(
            "engine-scenarios.jsonl",
            scenario="update_db_visibility",
            direction="A_to_B",
            operation="update",
            observer_state="live_without_restart",
            expected="B get returns new content",
            observed=b_get,
            passed=passed,
        )
        b_recall = await b.request("recall", query=tokens["update_a_new"])
        passed = _contains(b_recall, update_a["id"], tokens["update_a_new"])
        if not passed:
            failures.append("A_to_B update recall")
        _record(
            "engine-scenarios.jsonl",
            scenario="update_recall_freshness",
            direction="A_to_B",
            operation="update",
            observer_state="live_without_restart",
            expected="B recall returns updated content and not cached old content",
            observed=b_recall,
            passed=passed,
        )

        await b.request(
            "update", memory_id=update_b["id"], content=tokens["update_b_new"]
        )
        a_get = await a.request("get", memory_id=update_b["id"])
        passed = a_get is not None and a_get["content"] == tokens["update_b_new"]
        _record(
            "engine-scenarios.jsonl",
            scenario="update_db_visibility",
            direction="B_to_A",
            operation="update",
            observer_state="live_without_restart",
            expected="A get returns new content",
            observed=a_get,
            passed=passed,
        )
        a_recall = await a.request("recall", query=tokens["update_b_new"])
        passed = _contains(a_recall, update_b["id"], tokens["update_b_new"])
        if not passed:
            failures.append("B_to_A update recall")
        _record(
            "engine-scenarios.jsonl",
            scenario="update_recall_freshness",
            direction="B_to_A",
            operation="update",
            observer_state="live_without_restart",
            expected="A recall returns updated content and not cached old content",
            observed=a_recall,
            passed=passed,
        )

        assert await a.request("forget", memory_id=delete_a["id"]) is True
        b_get = await b.request("get", memory_id=delete_a["id"])
        _record(
            "engine-scenarios.jsonl",
            scenario="delete_db_visibility",
            direction="A_to_B",
            operation="delete",
            observer_state="live_without_restart",
            expected="B get returns no row",
            observed=b_get,
            passed=b_get is None,
        )
        b_recall = await b.request("recall", query=tokens["delete_a"])
        passed = not _contains(b_recall, delete_a["id"])
        if not passed:
            failures.append("A_to_B delete recall ghost")
        _record(
            "engine-scenarios.jsonl",
            scenario="delete_recall_ghost",
            direction="A_to_B",
            operation="delete",
            observer_state="live_without_restart",
            expected="B recall does not return deleted memory",
            observed=b_recall,
            passed=passed,
        )

        assert await b.request("forget", memory_id=delete_b["id"]) is True
        a_get = await a.request("get", memory_id=delete_b["id"])
        _record(
            "engine-scenarios.jsonl",
            scenario="delete_db_visibility",
            direction="B_to_A",
            operation="delete",
            observer_state="live_without_restart",
            expected="A get returns no row",
            observed=a_get,
            passed=a_get is None,
        )
        a_recall = await a.request("recall", query=tokens["delete_b"])
        passed = not _contains(a_recall, delete_b["id"])
        if not passed:
            failures.append("B_to_A delete recall ghost")
        _record(
            "engine-scenarios.jsonl",
            scenario="delete_recall_ghost",
            direction="B_to_A",
            operation="delete",
            observer_state="live_without_restart",
            expected="A recall does not return deleted memory",
            observed=a_recall,
            passed=passed,
        )

        await b.stop()
        b = EngineWorker(db_path, "B-restarted")
        await b.start()
        recovery_checks = [
            (
                "create",
                await b.request("recall", query=tokens["create_a"]),
                lambda rows: _contains(rows, created_a["id"], tokens["create_a"]),
            ),
            (
                "update",
                await b.request("recall", query=tokens["update_a_new"]),
                lambda rows: _contains(rows, update_a["id"], tokens["update_a_new"]),
            ),
            (
                "delete",
                await b.request("recall", query=tokens["delete_a"]),
                lambda rows: not _contains(rows, delete_a["id"]),
            ),
        ]
        for operation, observed, predicate in recovery_checks:
            passed = predicate(observed)
            if not passed:
                failures.append(f"A_to_B restart recovery {operation}")
            _record(
                "engine-scenarios.jsonl",
                scenario="restart_recovery",
                direction="A_to_B",
                operation=operation,
                observer_state="after_B_restart",
                expected="observer recall matches current SQLite state",
                observed=observed,
                passed=passed,
            )

        await a.stop()
        a = EngineWorker(db_path, "A-restarted")
        await a.start()
        recovery_checks = [
            (
                "create",
                await a.request("recall", query=tokens["create_b"]),
                lambda rows: _contains(rows, created_b["id"], tokens["create_b"]),
            ),
            (
                "update",
                await a.request("recall", query=tokens["update_b_new"]),
                lambda rows: _contains(rows, update_b["id"], tokens["update_b_new"]),
            ),
            (
                "delete",
                await a.request("recall", query=tokens["delete_b"]),
                lambda rows: not _contains(rows, delete_b["id"]),
            ),
        ]
        for operation, observed, predicate in recovery_checks:
            passed = predicate(observed)
            if not passed:
                failures.append(f"B_to_A restart recovery {operation}")
            _record(
                "engine-scenarios.jsonl",
                scenario="restart_recovery",
                direction="B_to_A",
                operation=operation,
                observer_state="after_A_restart",
                expected="observer recall matches current SQLite state",
                observed=observed,
                passed=passed,
            )
    finally:
        await a.stop()
        await b.stop()
        if db_path.exists():
            _sqlite_snapshot(db_path, "engine scenarios final state")

    assert not failures, "cross-process engine coherence failures: " + "; ".join(failures)


def _transport_env(db_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "SQLITE_DB_PATH": str(db_path),
            "EMBEDDER_MODE": "hash",
            "LEVH_MCP_PROFILE": "full",
            "PYTHONPATH": str(ROOT),
            "LEVH_AUTH_TOKEN": "",
        }
    )
    return env


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class RestProcess:
    def __init__(self, db_path: Path, label: str):
        self.db_path = db_path
        self.label = label
        self.port = _free_port()
        self.process: asyncio.subprocess.Process | None = None
        self._stdout = None
        self._stderr = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def start(self) -> None:
        self._stdout = (EVIDENCE / "stdout" / f"rest-{self.label}.log").open("ab")
        self._stderr = (EVIDENCE / "stderr" / f"rest-{self.label}.log").open("ab")
        self.process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "uvicorn",
            "server.api:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
            cwd=str(ROOT),
            env=_transport_env(self.db_path),
            stdout=self._stdout,
            stderr=self._stderr,
        )
        async with httpx.AsyncClient(timeout=1.0) as client:
            for _ in range(100):
                if self.process.returncode is not None:
                    raise AssertionError(f"REST {self.label} exited during startup")
                try:
                    response = await client.get(f"{self.base_url}/api/memories?limit=1")
                    if response.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.1)
            else:
                raise AssertionError(f"REST {self.label} did not become ready")
        _append_text(
            EVIDENCE / "process-map.txt",
            f"REST {self.label}: pid={self.process.pid} url={self.base_url} db={self.db_path}\n",
        )

    async def stop(self) -> None:
        if self.process is not None and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self._stdout is not None:
            self._stdout.close()
        if self._stderr is not None:
            self._stderr.close()
        self.process = None


def _tool_text(result: Any) -> str:
    return "\n".join(getattr(item, "text", "") for item in result.content)


@asynccontextmanager
async def _mcp_session(
    db_path: Path, label: str
) -> AsyncIterator[ClientSession]:
    err_path = EVIDENCE / "stderr" / f"mcp-{label}.log"
    with err_path.open("w", encoding="utf-8") as errlog:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "server.mcp_stdio"],
            env=_transport_env(db_path),
            cwd=str(ROOT),
        )
        _append_text(
            EVIDENCE / "process-map.txt",
            f"MCP {label}: transport=stdio launcher=mcp.client.stdio db={db_path} "
            "pid=not_exposed_by_client_helper\n",
        )
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


async def _rest_call(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
) -> Any:
    response = await client.request(method, path, json=json_body)
    response.raise_for_status()
    return response.json()


async def _mcp_call(
    session: ClientSession, tool: str, arguments: dict[str, Any], label: str
) -> str:
    result = await session.call_tool(tool, arguments)
    text = _tool_text(result)
    _append_jsonl(
        "stdout/mcp-tool-results.jsonl",
        {
            "label": label,
            "tool": tool,
            "arguments": arguments,
            "is_error": result.isError,
            "text": text,
        },
    )
    assert result.isError is False
    return text


@pytest.mark.asyncio
async def test_real_mcp_stdio_and_rest_cross_process_coherence(tmp_path: Path) -> None:
    _reset_files(
        "transport-scenarios.jsonl",
        "stdout/mcp-tool-results.jsonl",
        "stdout/rest-initial.log",
        "stdout/rest-restarted.log",
        "stderr/rest-initial.log",
        "stderr/rest-restarted.log",
        "stderr/mcp-initial.log",
        "stderr/mcp-restarted.log",
    )
    db_path = tmp_path / "transport-coherence.db"
    tokens = {
        "rest_update_old": "GT00A1_TRANSPORT_REST_UPDATE_OLD_25CB70 exact baseline",
        "rest_update_new": "GT00A1_TRANSPORT_REST_UPDATE_NEW_B8F316 exact replacement",
        "mcp_update_old": "GT00A1_TRANSPORT_MCP_UPDATE_OLD_D430A2 exact baseline",
        "mcp_update_new": "GT00A1_TRANSPORT_MCP_UPDATE_NEW_07E9C4 exact replacement",
        "rest_delete": "GT00A1_TRANSPORT_REST_DELETE_9461AC exact ghost probe",
        "mcp_delete": "GT00A1_TRANSPORT_MCP_DELETE_A11F83 exact ghost probe",
        "rest_create": "GT00A1_TRANSPORT_REST_CREATE_8DB532 exact created memory",
        "mcp_create": "VIOLET KESTREL AMBER TUNDRA 3F70C1 uniquely created fact",
    }
    seeded = await _seed(
        db_path,
        [
            tokens["rest_update_old"],
            tokens["mcp_update_old"],
            tokens["rest_delete"],
            tokens["mcp_delete"],
        ],
    )
    rest_update, mcp_update, rest_delete, mcp_delete = seeded
    rest = RestProcess(db_path, "initial")
    failures: list[str] = []
    rest_created: dict[str, Any] | None = None
    mcp_created_id = ""
    try:
        await rest.start()
        async with httpx.AsyncClient(base_url=rest.base_url, timeout=10) as client:
            async with _mcp_session(db_path, "initial") as mcp:
                rest_created = await _rest_call(
                    client,
                    "POST",
                    "/api/memories",
                    json_body={
                        "content": tokens["rest_create"],
                        "importance": 0.8,
                        "tags": ["groundtruth", "00A1"],
                        "project": PROJECT,
                        "memory_type": "episodic",
                        "force": True,
                    },
                )
                mcp_recall = await _mcp_call(
                    mcp,
                    "recall_memory",
                    {"query": tokens["rest_create"], "top_k": 10, "project": PROJECT},
                    "REST_to_MCP create live",
                )
                passed = rest_created["id"] in mcp_recall and tokens["rest_create"] in mcp_recall
                if not passed:
                    failures.append("REST_to_MCP create recall")
                _record(
                    "transport-scenarios.jsonl",
                    scenario="create_recall_visibility",
                    direction="REST_to_MCP_stdio",
                    operation="create",
                    observer_state="live_without_restart",
                    expected="MCP recall returns REST-created memory",
                    observed=mcp_recall,
                    passed=passed,
                )

                mcp_store = await _mcp_call(
                    mcp,
                    "store_memory",
                    {
                        "content": tokens["mcp_create"],
                        "importance": 0.8,
                        "tags": "groundtruth,00A1",
                        "project": PROJECT,
                        "memory_type": "episodic",
                    },
                    "MCP_to_REST create live",
                )
                match = re.search(r"ID:\s*([0-9a-fA-F-]{32,36})", mcp_store)
                assert match, f"MCP store did not return an ID: {mcp_store}"
                mcp_created_id = match.group(1)
                rest_get = await _rest_call(client, "GET", f"/api/memories/{mcp_created_id}")
                _record(
                    "transport-scenarios.jsonl",
                    scenario="create_db_visibility",
                    direction="MCP_stdio_to_REST",
                    operation="create",
                    observer_state="live_without_restart",
                    expected="REST GET returns MCP-created memory",
                    observed=rest_get,
                    passed=rest_get["content"] == tokens["mcp_create"],
                )
                rest_recall = await _rest_call(
                    client,
                    "POST",
                    "/api/memories/recall",
                    json_body={
                        "query": tokens["mcp_create"],
                        "top_k": 10,
                        "project": PROJECT,
                        "reinforce": False,
                    },
                )
                passed = _contains(rest_recall["memories"], mcp_created_id, tokens["mcp_create"])
                if not passed:
                    failures.append("MCP_to_REST create recall")
                _record(
                    "transport-scenarios.jsonl",
                    scenario="create_recall_visibility",
                    direction="MCP_stdio_to_REST",
                    operation="create",
                    observer_state="live_without_restart",
                    expected="REST recall returns MCP-created memory",
                    observed=rest_recall,
                    passed=passed,
                )

                await _rest_call(
                    client,
                    "PUT",
                    f"/api/memories/{rest_update['id']}",
                    json_body={"content": tokens["rest_update_new"]},
                )
                mcp_recall = await _mcp_call(
                    mcp,
                    "recall_memory",
                    {"query": tokens["rest_update_new"], "top_k": 10, "project": PROJECT},
                    "REST_to_MCP update live",
                )
                passed = rest_update["id"] in mcp_recall and tokens["rest_update_new"] in mcp_recall
                if not passed:
                    failures.append("REST_to_MCP update recall")
                _record(
                    "transport-scenarios.jsonl",
                    scenario="update_recall_freshness",
                    direction="REST_to_MCP_stdio",
                    operation="update",
                    observer_state="live_without_restart",
                    expected="MCP recall returns REST-updated content",
                    observed=mcp_recall,
                    passed=passed,
                )

                await _mcp_call(
                    mcp,
                    "update_memory",
                    {"memory_id": mcp_update["id"], "content": tokens["mcp_update_new"]},
                    "MCP_to_REST update live",
                )
                rest_get = await _rest_call(client, "GET", f"/api/memories/{mcp_update['id']}")
                _record(
                    "transport-scenarios.jsonl",
                    scenario="update_db_visibility",
                    direction="MCP_stdio_to_REST",
                    operation="update",
                    observer_state="live_without_restart",
                    expected="REST GET returns MCP-updated content",
                    observed=rest_get,
                    passed=rest_get["content"] == tokens["mcp_update_new"],
                )
                rest_recall = await _rest_call(
                    client,
                    "POST",
                    "/api/memories/recall",
                    json_body={
                        "query": tokens["mcp_update_new"],
                        "top_k": 10,
                        "project": PROJECT,
                        "reinforce": False,
                    },
                )
                passed = _contains(rest_recall["memories"], mcp_update["id"], tokens["mcp_update_new"])
                if not passed:
                    failures.append("MCP_to_REST update recall")
                _record(
                    "transport-scenarios.jsonl",
                    scenario="update_recall_freshness",
                    direction="MCP_stdio_to_REST",
                    operation="update",
                    observer_state="live_without_restart",
                    expected="REST recall returns MCP-updated content",
                    observed=rest_recall,
                    passed=passed,
                )

                await _rest_call(client, "DELETE", f"/api/memories/{rest_delete['id']}")
                mcp_recall = await _mcp_call(
                    mcp,
                    "recall_memory",
                    {"query": tokens["rest_delete"], "top_k": 10, "project": PROJECT},
                    "REST_to_MCP delete live",
                )
                passed = rest_delete["id"] not in mcp_recall
                if not passed:
                    failures.append("REST_to_MCP delete recall ghost")
                _record(
                    "transport-scenarios.jsonl",
                    scenario="delete_recall_ghost",
                    direction="REST_to_MCP_stdio",
                    operation="delete",
                    observer_state="live_without_restart",
                    expected="MCP recall omits REST-deleted memory",
                    observed=mcp_recall,
                    passed=passed,
                )

                await _mcp_call(
                    mcp,
                    "forget_memory",
                    {"memory_id": mcp_delete["id"]},
                    "MCP_to_REST delete live",
                )
                missing = await client.get(f"/api/memories/{mcp_delete['id']}")
                _record(
                    "transport-scenarios.jsonl",
                    scenario="delete_db_visibility",
                    direction="MCP_stdio_to_REST",
                    operation="delete",
                    observer_state="live_without_restart",
                    expected="REST GET returns 404",
                    observed={"status_code": missing.status_code},
                    passed=missing.status_code == 404,
                )
                rest_recall = await _rest_call(
                    client,
                    "POST",
                    "/api/memories/recall",
                    json_body={
                        "query": tokens["mcp_delete"],
                        "top_k": 10,
                        "project": PROJECT,
                        "reinforce": False,
                    },
                )
                passed = not _contains(rest_recall["memories"], mcp_delete["id"])
                if not passed:
                    failures.append("MCP_to_REST delete recall ghost")
                _record(
                    "transport-scenarios.jsonl",
                    scenario="delete_recall_ghost",
                    direction="MCP_stdio_to_REST",
                    operation="delete",
                    observer_state="live_without_restart",
                    expected="REST recall omits MCP-deleted memory",
                    observed=rest_recall,
                    passed=passed,
                )

            async with _mcp_session(db_path, "restarted") as mcp:
                for operation, memory_id, query, must_exist in [
                    ("create", rest_created["id"], tokens["rest_create"], True),
                    ("update", rest_update["id"], tokens["rest_update_new"], True),
                    ("delete", rest_delete["id"], tokens["rest_delete"], False),
                ]:
                    observed = await _mcp_call(
                        mcp,
                        "recall_memory",
                        {"query": query, "top_k": 10, "project": PROJECT},
                        f"REST_to_MCP {operation} after restart",
                    )
                    passed = (memory_id in observed) == must_exist
                    if must_exist:
                        passed = passed and query in observed
                    if not passed:
                        failures.append(f"REST_to_MCP restart recovery {operation}")
                    _record(
                        "transport-scenarios.jsonl",
                        scenario="restart_recovery",
                        direction="REST_to_MCP_stdio",
                        operation=operation,
                        observer_state="after_MCP_restart",
                        expected="MCP recall matches current SQLite state",
                        observed=observed,
                        passed=passed,
                    )

        await rest.stop()
        rest = RestProcess(db_path, "restarted")
        await rest.start()
        async with httpx.AsyncClient(base_url=rest.base_url, timeout=10) as client:
            for operation, memory_id, query, must_exist in [
                ("create", mcp_created_id, tokens["mcp_create"], True),
                ("update", mcp_update["id"], tokens["mcp_update_new"], True),
                ("delete", mcp_delete["id"], tokens["mcp_delete"], False),
            ]:
                observed = await _rest_call(
                    client,
                    "POST",
                    "/api/memories/recall",
                    json_body={
                        "query": query,
                        "top_k": 10,
                        "project": PROJECT,
                        "reinforce": False,
                    },
                )
                passed = _contains(observed["memories"], memory_id, query) if must_exist else not _contains(observed["memories"], memory_id)
                if not passed:
                    failures.append(f"MCP_to_REST restart recovery {operation}")
                _record(
                    "transport-scenarios.jsonl",
                    scenario="restart_recovery",
                    direction="MCP_stdio_to_REST",
                    operation=operation,
                    observer_state="after_REST_restart",
                    expected="REST recall matches current SQLite state",
                    observed=observed,
                    passed=passed,
                )
    finally:
        await rest.stop()
        if db_path.exists():
            _sqlite_snapshot(db_path, "transport scenarios final state")

    assert not failures, "cross-process transport coherence failures: " + "; ".join(failures)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--engine-worker":
        asyncio.run(_worker_main(sys.argv[2], sys.argv[3]))
    else:
        raise SystemExit("This module is run by pytest or with --engine-worker DB LABEL")
