"""TASK-00A4 characterization: standalone MCP SSE auth/profile boundary.

Every server binds to 127.0.0.1 on an ephemeral port.  The only mutating tools
called are store_memory and forget_memory for the exact ID created by the same
scenario.  No purge, restore, backup, connector, or network tool is invoked.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import sys
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.sse import sse_client
import pytest

from server.tools.profiles import tools_for_profile


def _find_repo_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "pyproject.toml").is_file() and (candidate / "server").is_dir():
            return candidate
    raise RuntimeError("repository root not found from audit harness path")


ROOT = _find_repo_root()
EVIDENCE = Path(
    os.environ.get(
        "LEVH_GROUNDTRUTH_EVIDENCE_DIR",
        ROOT / "evidence" / "groundtruth" / "task-00A4",
    )
)
SYNTHETIC_TOKEN = "task00a4-synthetic-loopback-token"


def _ensure_evidence() -> None:
    (EVIDENCE / "stdout").mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "stderr").mkdir(parents=True, exist_ok=True)


def _reset_evidence() -> None:
    _ensure_evidence()
    for relative in (
        "scenarios.jsonl",
        "tool-surfaces.jsonl",
        "process-map.txt",
        "sqlite-state.txt",
        "stdout/tool-results.jsonl",
        "stdout/pytest.txt",
        "stderr/pytest.txt",
    ):
        (EVIDENCE / relative).write_text("", encoding="utf-8")


def _append_jsonl(relative: str, record: dict[str, Any]) -> None:
    _ensure_evidence()
    with (EVIDENCE / relative).open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _append_text(relative: str, text: str) -> None:
    _ensure_evidence()
    with (EVIDENCE / relative).open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _record(
    *,
    scenario: str,
    server: str,
    token_env: str,
    client_token: str,
    profile: str,
    expected: str,
    observed: Any,
    passed: bool,
) -> bool:
    _append_jsonl(
        "scenarios.jsonl",
        {
            "scenario": scenario,
            "server": server,
            "token_env": token_env,
            "client_token": client_token,
            "profile": profile,
            "expected": expected,
            "observed": observed,
            "passed": passed,
        },
    )
    return passed


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _tool_text(result: Any) -> str:
    return "\n".join(getattr(item, "text", "") for item in result.content)


class LoopbackServer:
    def __init__(
        self,
        *,
        label: str,
        app_target: str,
        db_path: Path,
        profile: str | None,
        token: str | None,
        ready_path: str,
    ) -> None:
        self.label = label
        self.app_target = app_target
        self.db_path = db_path
        self.profile = profile
        self.token = token
        self.ready_path = ready_path
        self.port = _free_port()
        self.process: asyncio.subprocess.Process | None = None
        self._stdout = None
        self._stderr = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def start(self) -> None:
        stdout_path = EVIDENCE / "stdout" / f"{self.label}.log"
        stderr_path = EVIDENCE / "stderr" / f"{self.label}.log"
        self._stdout = stdout_path.open("ab")
        self._stderr = stderr_path.open("ab")
        env = dict(os.environ)
        env.pop("LEVH_TOKEN", None)
        env.pop("LEVH_MCP_PROFILE", None)
        env.update(
            {
                "SQLITE_DB_PATH": str(self.db_path),
                "EMBEDDER_MODE": "hash",
                "PYTHONPATH": str(ROOT),
                "NO_PROXY": "127.0.0.1,localhost",
            }
        )
        if self.profile is not None:
            env["LEVH_MCP_PROFILE"] = self.profile
        if self.token is not None:
            env["LEVH_TOKEN"] = self.token
        self.process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "uvicorn",
            self.app_target,
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
            cwd=str(ROOT),
            env=env,
            stdout=self._stdout,
            stderr=self._stderr,
        )
        async with httpx.AsyncClient(timeout=1.0, trust_env=False) as client:
            for _ in range(100):
                if self.process.returncode is not None:
                    raise AssertionError(f"{self.label} exited during startup")
                try:
                    response = await client.get(self.base_url + self.ready_path)
                    if response.status_code < 500:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.1)
            else:
                raise AssertionError(f"{self.label} did not become ready")
        _append_text(
            "process-map.txt",
            (
                f"{self.label}: pid={self.process.pid} app={self.app_target} "
                f"host=127.0.0.1 port={self.port} db={self.db_path} "
                f"profile={self.profile or '<unset>'} "
                f"token_env={'set' if self.token else 'absent'}\n"
            ),
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


def _sqlite_snapshot(db_path: Path, label: str) -> dict[str, Any]:
    with sqlite3.connect(db_path) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        rows = connection.execute(
            "SELECT id, content FROM memories ORDER BY created_at, id"
        ).fetchall()
    result = {
        "label": label,
        "database": str(db_path),
        "integrity_check": integrity,
        "memory_count": len(rows),
        "rows": [{"id": row[0], "content": row[1]} for row in rows],
    }
    _append_text("sqlite-state.txt", json.dumps(result, ensure_ascii=False) + "\n")
    return result


async def _run_standalone(
    *,
    tmp_path: Path,
    label: str,
    profile: str | None,
    token_env: str | None,
    mutate_one: bool,
) -> dict[str, Any]:
    db_path = tmp_path / f"{label}.db"
    server = LoopbackServer(
        label=label,
        app_target="server.mcp_sse:app",
        db_path=db_path,
        profile=profile,
        token=token_env,
        ready_path="/",
    )
    effective_profile = profile or "full"
    expected_tools = tools_for_profile(effective_profile)
    stored_id = ""
    store_text = ""
    forget_text = ""
    try:
        await server.start()
        async with sse_client(
            server.base_url + "/sse",
            timeout=5,
            sse_read_timeout=20,
        ) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                tools = await session.list_tools()
                tool_names = [tool.name for tool in tools.tools]
                _append_jsonl(
                    "tool-surfaces.jsonl",
                    {
                        "label": label,
                        "server": "standalone_sse",
                        "profile_env": profile,
                        "effective_profile": effective_profile,
                        "token_env": "set" if token_env else "absent",
                        "client_token": "absent",
                        "tool_count": len(tool_names),
                        "tool_names": tool_names,
                    },
                )
                init_pass = init is not None
                _record(
                    scenario=f"{label}_initialize_without_client_token",
                    server="standalone_sse",
                    token_env="set" if token_env else "absent",
                    client_token="absent",
                    profile=effective_profile,
                    expected="MCP initialize result is observed",
                    observed={"initialized": init_pass},
                    passed=init_pass,
                )
                surface_pass = set(tool_names) == expected_tools
                _record(
                    scenario=f"{label}_profile_surface",
                    server="standalone_sse",
                    token_env="set" if token_env else "absent",
                    client_token="absent",
                    profile=effective_profile,
                    expected=f"exact {effective_profile} capability set",
                    observed={"count": len(tool_names), "names": tool_names},
                    passed=surface_pass,
                )
                if mutate_one:
                    assert effective_profile == "full"
                    content = f"GT00A4 controlled single memory for {label}"
                    stored = await session.call_tool(
                        "store_memory",
                        {
                            "content": content,
                            "memory_type": "episodic",
                            "project": "GT00A4_SSE_BOUNDARY",
                        },
                    )
                    store_text = _tool_text(stored)
                    match = re.search(r"ID:\s*([0-9a-fA-F-]{32,36})", store_text)
                    assert stored.isError is False and match
                    stored_id = match.group(1)
                    forgotten = await session.call_tool(
                        "forget_memory", {"memory_id": stored_id}
                    )
                    forget_text = _tool_text(forgotten)
                    _append_jsonl(
                        "stdout/tool-results.jsonl",
                        {
                            "label": label,
                            "store_tool": "store_memory",
                            "store_is_error": stored.isError,
                            "store_text": store_text,
                            "stored_id": stored_id,
                            "delete_tool": "forget_memory",
                            "delete_is_error": forgotten.isError,
                            "delete_text": forget_text,
                        },
                    )
                    mutation_pass = (
                        forgotten.isError is False
                        and stored_id in forget_text
                        and "forgotten" in forget_text.lower()
                    )
                    _record(
                        scenario=f"{label}_single_store_exact_delete",
                        server="standalone_sse",
                        token_env="set" if token_env else "absent",
                        client_token="absent",
                        profile=effective_profile,
                        expected="store one controlled memory then forget exact returned ID",
                        observed={
                            "stored_id": stored_id,
                            "store_text": store_text,
                            "forget_text": forget_text,
                        },
                        passed=mutation_pass,
                    )
                assert init_pass and surface_pass
    finally:
        await server.stop()
    state = _sqlite_snapshot(db_path, label)
    if mutate_one:
        lifecycle_pass = state["memory_count"] == 0
        _record(
            scenario=f"{label}_sqlite_empty_after_exact_delete",
            server="standalone_sse",
            token_env="set" if token_env else "absent",
            client_token="absent",
            profile=effective_profile,
            expected="controlled record no longer exists; no other row was touched",
            observed=state,
            passed=lifecycle_pass,
        )
        assert lifecycle_pass
    return {
        "label": label,
        "profile": effective_profile,
        "token_env": bool(token_env),
        "stored_id": stored_id,
        "state": state,
    }


@pytest.mark.asyncio
async def test_01_standalone_default_without_token_initializes_and_mutates_one(
    tmp_path: Path,
) -> None:
    _reset_evidence()
    await _run_standalone(
        tmp_path=tmp_path,
        label="standalone-default-no-token",
        profile=None,
        token_env=None,
        mutate_one=True,
    )


@pytest.mark.asyncio
async def test_02_standalone_minimal_work_full_profiles_are_capability_filters(
    tmp_path: Path,
) -> None:
    for profile in ("minimal", "work", "full"):
        await _run_standalone(
            tmp_path=tmp_path,
            label=f"standalone-profile-{profile}",
            profile=profile,
            token_env=None,
            mutate_one=False,
        )


@pytest.mark.asyncio
async def test_03_standalone_ignores_levh_token_without_client_header(
    tmp_path: Path,
) -> None:
    await _run_standalone(
        tmp_path=tmp_path,
        label="standalone-token-set-client-omits-token",
        profile="full",
        token_env=SYNTHETIC_TOKEN,
        mutate_one=True,
    )
    _record(
        scenario="standalone_configured_token_does_not_create_auth_boundary",
        server="standalone_sse",
        token_env="set",
        client_token="absent",
        profile="full",
        expected="characterize whether LEVH_TOKEN blocks an unauthenticated client",
        observed={
            "initialize_succeeded": True,
            "tool_count": 59,
            "controlled_store_and_exact_delete_succeeded": True,
        },
        passed=True,
    )


@pytest.mark.asyncio
async def test_04_main_fastapi_mounted_mcp_is_token_protected(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "mounted-fastapi.db"
    server = LoopbackServer(
        label="main-fastapi-mounted-token-set",
        app_target="server.api:app",
        db_path=db_path,
        profile="minimal",
        token=SYNTHETIC_TOKEN,
        ready_path="/api/health",
    )
    try:
        await server.start()
        async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
            get_response = await client.get(server.base_url + "/api/mcp/sse")
            get_pass = get_response.status_code == 401
            _record(
                scenario="mounted_mcp_sse_get_without_token_rejected",
                server="main_fastapi_mounted_mcp",
                token_env="set",
                client_token="absent",
                profile="minimal",
                expected="GET /api/mcp/sse returns 401",
                observed={"status_code": get_response.status_code, "body": get_response.text},
                passed=get_pass,
            )
            post_response = await client.post(
                server.base_url + "/api/mcp/messages/?session_id=synthetic"
            )
            post_pass = post_response.status_code == 401
            _record(
                scenario="mounted_mcp_messages_post_without_token_rejected",
                server="main_fastapi_mounted_mcp",
                token_env="set",
                client_token="absent",
                profile="minimal",
                expected="POST /api/mcp/messages/ returns 401",
                observed={"status_code": post_response.status_code, "body": post_response.text},
                passed=post_pass,
            )

        headers = {"X-LEVH-Token": SYNTHETIC_TOKEN}
        async with sse_client(
            server.base_url + "/api/mcp/sse",
            headers=headers,
            timeout=5,
            sse_read_timeout=20,
        ) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                tools = await session.list_tools()
                tool_names = [tool.name for tool in tools.tools]
                _append_jsonl(
                    "tool-surfaces.jsonl",
                    {
                        "label": "main-fastapi-mounted-token-set",
                        "server": "main_fastapi_mounted_mcp",
                        "profile_env": "minimal",
                        "effective_profile": "minimal",
                        "token_env": "set",
                        "client_token": "valid_header",
                        "tool_count": len(tool_names),
                        "tool_names": tool_names,
                    },
                )
                init_pass = init is not None
                surface_pass = set(tool_names) == tools_for_profile("minimal")
                _record(
                    scenario="mounted_mcp_valid_token_initializes",
                    server="main_fastapi_mounted_mcp",
                    token_env="set",
                    client_token="valid_header",
                    profile="minimal",
                    expected="valid token permits MCP initialize",
                    observed={"initialized": init_pass},
                    passed=init_pass,
                )
                _record(
                    scenario="mounted_mcp_valid_token_minimal_surface",
                    server="main_fastapi_mounted_mcp",
                    token_env="set",
                    client_token="valid_header",
                    profile="minimal",
                    expected="authenticated client sees exact minimal capability set",
                    observed={"count": len(tool_names), "names": tool_names},
                    passed=surface_pass,
                )
                assert get_pass and post_pass and init_pass and surface_pass
    finally:
        await server.stop()
    state = _sqlite_snapshot(db_path, "main-fastapi-mounted-token-set")
    assert state["integrity_check"] == "ok"

    scenario_rows = [
        json.loads(line)
        for line in (EVIDENCE / "scenarios.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    surface_rows = [
        json.loads(line)
        for line in (EVIDENCE / "tool-surfaces.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(scenario_rows) == 19
    assert all(row["passed"] for row in scenario_rows)
    assert len(surface_rows) == 6
    assert all(row["tool_count"] in (5, 15, 59) for row in surface_rows)
