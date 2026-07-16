"""StackMemory CLI Tests — focused coverage for doctor, init, MCP config commands.

Tests:
  1. stackmemory doctor exits 0 in hash mode
  2. stackmemory init creates config
  3. init does not overwrite existing config without --force
  4. mcp config claude outputs valid JSON
  5. mcp config cursor outputs valid JSON
  6. mcp config windsurf outputs valid JSON
  7. generated JSON contains EMBEDDER_MODE=hash
  8. generated config uses real executable/args
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure EMBEDDER_MODE=hash for all tests
os.environ["EMBEDDER_MODE"] = "hash"


def _run_cli(
    *args: str,
    cwd: str | None = None,
    timeout: int = 30,
    extra_env: dict | None = None,
) -> subprocess.CompletedProcess:
    """Run the CLI via python -m server.cli with given arguments."""
    cmd = [sys.executable, "-m", "server.cli", *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
        env={**os.environ, **(extra_env or {})},
    )


class TestDoctor:
    """Test 1: stackmemory doctor exits 0 in hash mode."""

    def test_doctor_exits_zero(self):
        result = _run_cli("doctor")
        assert result.returncode == 0, f"doctor failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        assert "StackMemory Doctor" in result.stdout
        assert "Verdict: OK" in result.stdout

    def test_doctor_checks_all_components(self):
        result = _run_cli("doctor")
        assert result.returncode == 0
        # Verify key check names appear
        for name in ["Python", "Package import", "Database path", "API import", "MCP import", "Embedder mode"]:
            assert name in result.stdout, f"Missing check: {name}"


class TestInit:
    """Test 2 & 3: init creates config, does not overwrite without --force."""

    def test_init_creates_config(self, tmp_path):
        result = _run_cli("init", cwd=str(tmp_path))
        assert result.returncode == 0, f"init failed:\n{result.stderr}"
        config_file = tmp_path / ".stackmemory" / "config.json"
        assert config_file.exists(), "config.json not created"

        with open(config_file) as f:
            cfg = json.load(f)
        assert cfg["embedder_mode"] == "hash"
        assert "database_path" in cfg
        assert "api_host" in cfg
        assert "api_port" in cfg

    def test_init_creates_mcp_dir(self, tmp_path):
        result = _run_cli("init", cwd=str(tmp_path))
        assert result.returncode == 0
        mcp_dir = tmp_path / ".stackmemory" / "mcp"
        assert mcp_dir.exists(), "mcp/ directory not created"

    def test_init_no_overwrite_without_force(self, tmp_path):
        # First init
        r1 = _run_cli("init", cwd=str(tmp_path))
        assert r1.returncode == 0
        config_file = tmp_path / ".stackmemory" / "config.json"

        # Modify config
        with open(config_file) as f:
            cfg = json.load(f)
        cfg["embedder_mode"] = "custom_value"
        with open(config_file, "w") as f:
            json.dump(cfg, f)

        # Second init without --force should fail
        r2 = _run_cli("init", cwd=str(tmp_path))
        assert r2.returncode == 1, "init should fail when config exists without --force"
        assert "already exists" in r2.stdout

        # Verify config unchanged
        with open(config_file) as f:
            cfg2 = json.load(f)
        assert cfg2["embedder_mode"] == "custom_value", "config was overwritten without --force"

    def test_init_force_overwrites(self, tmp_path):
        r1 = _run_cli("init", cwd=str(tmp_path))
        assert r1.returncode == 0
        r2 = _run_cli("init", "--force", cwd=str(tmp_path))
        assert r2.returncode == 0, "init --force should succeed"

    def test_init_custom_embedder_mode(self, tmp_path):
        result = _run_cli("init", "--embedder-mode", "local", cwd=str(tmp_path))
        assert result.returncode == 0
        config_file = tmp_path / ".stackmemory" / "config.json"
        with open(config_file) as f:
            cfg = json.load(f)
        assert cfg["embedder_mode"] == "local"


class TestMcpConfig:
    """Tests 4-8: MCP config generation for each client."""

    def _assert_valid_mcp_config(self, output: str):
        """Assert the output is valid MCP config JSON."""
        data = json.loads(output)
        assert "mcpServers" in data, "Missing mcpServers key"
        assert "stackmemory" in data["mcpServers"], "Missing stackmemory server entry"

        server = data["mcpServers"]["stackmemory"]
        assert "command" in server, "Missing 'command' in server entry"
        assert "args" in server, "Missing 'args' in server entry"
        assert "cwd" in server, "Missing 'cwd' in server entry"
        assert "env" in server, "Missing 'env' in server entry"
        return server

    def test_config_claude_valid_json(self):
        result = _run_cli("mcp", "config", "claude")
        assert result.returncode == 0, f"claude config failed:\n{result.stderr}"
        server = self._assert_valid_mcp_config(result.stdout)

    def test_config_cursor_valid_json(self):
        result = _run_cli("mcp", "config", "cursor")
        assert result.returncode == 0, f"cursor config failed:\n{result.stderr}"
        server = self._assert_valid_mcp_config(result.stdout)

    def test_config_windsurf_valid_json(self):
        result = _run_cli("mcp", "config", "windsurf")
        assert result.returncode == 0, f"windsurf config failed:\n{result.stderr}"
        server = self._assert_valid_mcp_config(result.stdout)

    def test_config_contains_embedder_mode_hash(self):
        """All generated configs should default to EMBEDDER_MODE=hash."""
        for platform in ["claude", "cursor", "windsurf"]:
            result = _run_cli("mcp", "config", platform)
            assert result.returncode == 0
            data = json.loads(result.stdout)
            env = data["mcpServers"]["stackmemory"]["env"]
            assert env.get("EMBEDDER_MODE") == "hash", (
                f"{platform} config: expected EMBEDDER_MODE=hash, got {env.get('EMBEDDER_MODE')}"
            )

    def test_config_uses_real_executable(self):
        """Config should use the real Python executable, not a hardcoded path."""
        result = _run_cli("mcp", "config", "claude")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        command = data["mcpServers"]["stackmemory"]["command"]
        # Should be sys.executable, not just "python"
        assert command == sys.executable, f"Expected {sys.executable}, got {command}"

    def test_config_uses_mcp_stdio_args(self):
        """Args should reference server.mcp_stdio module."""
        result = _run_cli("mcp", "config", "claude")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        args = data["mcpServers"]["stackmemory"]["args"]
        assert args == ["-m", "server.mcp_stdio"], f"Expected ['-m', 'server.mcp_stdio'], got {args}"

    def test_config_custom_embedder_override(self):
        """--embedder-mode flag should override the default."""
        result = _run_cli("mcp", "config", "claude", "--embedder-mode", "local")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        env = data["mcpServers"]["stackmemory"]["env"]
        assert env.get("EMBEDDER_MODE") == "local"

    def test_config_unknown_platform_fails(self):
        result = _run_cli("mcp", "config", "nonexistent_client")
        assert result.returncode == 1, "Unknown platform should fail with exit code 1"
        assert "Unknown platform" in result.stderr


class TestBenchmark:
    """stackmemory benchmark runs the recall-quality harness."""

    def test_benchmark_runs_and_reports_metrics(self):
        result = _run_cli("benchmark", timeout=60)
        assert result.returncode == 0, f"benchmark failed:\n{result.stderr}"
        for key in ("hit@1", "hit@3", "hit@5", "mrr"):
            assert key in result.stdout

    def test_benchmark_accepts_embedder_mode_override(self):
        result = _run_cli("benchmark", "--embedder-mode", "hash", timeout=60)
        assert result.returncode == 0
        assert "embedder_mode  hash" in result.stdout


class TestSummarize:
    """stackmemory summarize <session_id> distills a session."""

    def test_summarize_unknown_session_fails(self, tmp_path):
        db_path = str(tmp_path / "sm.db")
        result = _run_cli(
            "summarize", "nonexistent-session-id",
            timeout=30,
            extra_env={"SQLITE_DB_PATH": db_path},
        )
        assert result.returncode == 1
        assert "not found" in (result.stdout + result.stderr)
