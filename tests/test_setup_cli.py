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


def test_setup_requires_explicit_mode_when_noninteractive(tmp_path):
    r = _run("setup", cwd=tmp_path, db_path=tmp_path / "x.db")
    assert r.returncode == 1
    assert "--demo or --real" in r.stderr


def test_setup_real_is_repeat_safe_and_does_not_seed(tmp_path):
    db = tmp_path / "real.db"
    first = _run("setup", "--real", "--client", "claude", "--profile", "work", cwd=tmp_path, db_path=db)
    assert first.returncode == 0, first.stderr
    second = _run("setup", "--real", "--client", "claude", "--profile", "work", cwd=tmp_path, db_path=db)
    assert second.returncode == 0, second.stderr
    status = _run("setup", "--status", cwd=tmp_path, db_path=db)
    data = json.loads(status.stdout)
    assert data["memory_count"] == 0
    assert data["mcp_default_profile"] == "work"
    assert (tmp_path / ".stackmemory" / "mcp" / "claude-work.json").exists()
    receipt = json.loads((tmp_path / ".stackmemory" / "onboarding-receipt.json").read_text(encoding="utf-8"))
    assert receipt["demo_mode"] is False
    assert receipt["mcp_profile"] == "work"


def test_setup_demo_seeds_deterministic_shape_and_never_overwrites(tmp_path):
    db = tmp_path / "demo.db"
    r = _run("setup", "--demo", "--client", "cursor", "--profile", "minimal", cwd=tmp_path, db_path=db)
    assert r.returncode == 0, r.stderr
    status = _run("setup", "--status", cwd=tmp_path, db_path=db)
    data = json.loads(status.stdout)
    assert data["memory_count"] == 20
    assert data["demo_memory_count"] == 20
    assert data["demo_seeded"] is True
    assert "minimal" in (tmp_path / ".stackmemory" / "mcp" / "cursor-minimal.json").read_text(encoding="utf-8")

    again = _run("setup", "--demo", "--client", "cursor", "--profile", "minimal", cwd=tmp_path, db_path=db)
    assert again.returncode == 0
    status2 = json.loads(_run("setup", "--status", cwd=tmp_path, db_path=db).stdout)
    assert status2["memory_count"] == 20


def test_setup_invalid_profile_and_client_fail_cleanly(tmp_path):
    db = tmp_path / "bad.db"
    bad_profile = _run("setup", "--real", "--profile", "nonsense", cwd=tmp_path, db_path=db)
    assert bad_profile.returncode == 1
    assert "unknown MCP profile" in bad_profile.stderr
    bad_client = _run("setup", "--real", "--client", "nonsense", cwd=tmp_path, db_path=db)
    assert bad_client.returncode == 1
    assert "Unknown platform" in bad_client.stderr
