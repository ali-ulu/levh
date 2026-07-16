from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run(*args: str, cwd: Path, extra_env: dict[str, str] | None = None):
    env = dict(os.environ)
    env.pop("SQLITE_DB_PATH", None)
    env.pop("EMBEDDER_MODE", None)
    env.pop("SHORT_TERM_MAX", None)
    env["PYTHONPATH"] = str(REPO_ROOT)
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, "-m", "server.cli", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )


def test_init_custom_db_path_is_used_by_capture_and_mcp_config(tmp_path):
    custom = tmp_path / "data" / "configured.db"
    init = _run(
        "init",
        "--db-path",
        str(custom),
        "--embedder-mode",
        "hash",
        cwd=tmp_path,
    )
    assert init.returncode == 0, init.stderr

    captured = _run("capture", "configured database path works", cwd=tmp_path)
    assert captured.returncode == 0, captured.stderr
    assert custom.exists()
    assert not (tmp_path / "stackmemory.db").exists()

    mcp = _run("mcp", "config", "claude", cwd=tmp_path)
    assert mcp.returncode == 0, mcp.stderr
    data = json.loads(mcp.stdout)
    env = data["mcpServers"]["stackmemory"]["env"]
    assert env["SQLITE_DB_PATH"] == str(custom.resolve())
    assert env["EMBEDDER_MODE"] == "hash"


def test_environment_overrides_saved_config_for_runtime_and_generated_mcp(tmp_path):
    configured = tmp_path / "configured.db"
    override = tmp_path / "override.db"
    init = _run("init", "--db-path", str(configured), cwd=tmp_path)
    assert init.returncode == 0

    capture = _run(
        "capture",
        "environment wins",
        cwd=tmp_path,
        extra_env={"SQLITE_DB_PATH": str(override), "EMBEDDER_MODE": "hash"},
    )
    assert capture.returncode == 0, capture.stderr
    assert override.exists()
    assert not configured.exists()

    mcp = _run(
        "mcp",
        "config",
        "cursor",
        cwd=tmp_path,
        extra_env={"SQLITE_DB_PATH": str(override), "EMBEDDER_MODE": "hash"},
    )
    assert mcp.returncode == 0
    data = json.loads(mcp.stdout)
    assert data["mcpServers"]["stackmemory"]["env"]["SQLITE_DB_PATH"] == str(override.resolve())
