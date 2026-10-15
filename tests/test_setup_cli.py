from __future__ import annotations

import json
import os
import subprocess
import sys


def _run(*args: str, cwd, db_path, timeout=90):
    env = {
        **os.environ,
        "EMBEDDER_MODE": "hash",
        "SQLITE_DB_PATH": str(db_path),
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
    }
    return subprocess.run(
        [sys.executable, "-m", "server.cli", *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_setup_prepares_an_empty_store_without_a_mode_flag(tmp_path):
    """Setup needs no demo/real choice: it prepares an empty store."""
    db = tmp_path / "empty.db"
    r = _run("setup", "--client", "cursor", "--profile", "minimal", cwd=tmp_path, db_path=db)
    assert r.returncode == 0, r.stderr
    assert "Mode:" not in r.stdout
    assert "Demo data" not in r.stdout
    data = json.loads(_run("setup", "--status", cwd=tmp_path, db_path=db).stdout)
    assert data["memory_count"] == 0
    assert "demo_memory_count" not in data
    assert "demo_seeded" not in data
    assert "minimal" in (tmp_path / ".stackmemory" / "mcp" / "cursor-minimal.json").read_text(encoding="utf-8")


def test_setup_is_repeat_safe_and_does_not_seed(tmp_path):
    db = tmp_path / "real.db"
    first = _run("setup", "--client", "claude", "--profile", "work", cwd=tmp_path, db_path=db)
    assert first.returncode == 0, first.stderr
    second = _run("setup", "--client", "claude", "--profile", "work", cwd=tmp_path, db_path=db)
    assert second.returncode == 0, second.stderr
    status = _run("setup", "--status", cwd=tmp_path, db_path=db)
    data = json.loads(status.stdout)
    assert data["memory_count"] == 0
    assert data["mcp_default_profile"] == "work"
    assert (tmp_path / ".stackmemory" / "mcp" / "claude-work.json").exists()
    receipt = json.loads((tmp_path / ".stackmemory" / "onboarding-receipt.json").read_text(encoding="utf-8"))
    assert "demo_mode" not in receipt
    assert receipt["mcp_profile"] == "work"


def test_setup_invalid_profile_and_client_fail_cleanly(tmp_path):
    db = tmp_path / "bad.db"
    bad_profile = _run("setup", "--profile", "nonsense", cwd=tmp_path, db_path=db)
    assert bad_profile.returncode == 1
    assert "unknown MCP profile" in bad_profile.stderr
    bad_client = _run("setup", "--client", "nonsense", cwd=tmp_path, db_path=db)
    assert bad_client.returncode == 1
    assert "Unknown platform" in bad_client.stderr
