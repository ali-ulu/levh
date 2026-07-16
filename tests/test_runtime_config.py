from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.core.runtime_config import RuntimeConfigError, resolve_runtime_config


def _write_config(root: Path, **values):
    cfg_dir = root / ".stackmemory"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.json").write_text(json.dumps(values), encoding="utf-8")


def test_precedence_explicit_over_env_over_file_over_defaults(tmp_path):
    _write_config(
        tmp_path,
        database_path="from-file.db",
        embedder_mode="local",
        short_term_max=11,
        api_host="127.0.0.2",
        api_port=8100,
    )
    env = {
        "SQLITE_DB_PATH": "from-env.db",
        "EMBEDDER_MODE": "hash",
        "SHORT_TERM_MAX": "22",
        "API_PORT": "8200",
    }
    cfg = resolve_runtime_config(
        cwd=tmp_path,
        environ=env,
        explicit={"database_path": "from-explicit.db", "api_port": 8300},
    )
    assert cfg.database_path == str((tmp_path / "from-explicit.db").resolve())
    assert cfg.embedder_mode == "hash"
    assert cfg.short_term_max == 22
    assert cfg.api_host == "127.0.0.2"
    assert cfg.api_port == 8300


def test_config_database_path_is_resolved_from_working_directory(tmp_path):
    _write_config(tmp_path, database_path="data/custom.db")
    cfg = resolve_runtime_config(cwd=tmp_path, environ={})
    assert cfg.database_path == str((tmp_path / "data" / "custom.db").resolve())


def test_custom_config_path_environment_is_honored(tmp_path):
    other = tmp_path / "config.json"
    other.write_text(json.dumps({"database_path": "selected.db"}), encoding="utf-8")
    cfg = resolve_runtime_config(
        cwd=tmp_path,
        environ={"LEVH_CONFIG_PATH": str(other)},
    )
    assert cfg.config_path == str(other.resolve())
    assert cfg.database_path == str((tmp_path / "selected.db").resolve())


def test_present_malformed_config_fails_closed(tmp_path):
    cfg_dir = tmp_path / ".stackmemory"
    cfg_dir.mkdir()
    (cfg_dir / "config.json").write_text("{not-json", encoding="utf-8")
    with pytest.raises(RuntimeConfigError, match="Invalid LEVH config"):
        resolve_runtime_config(cwd=tmp_path, environ={})


def test_invalid_integer_values_fail_clearly(tmp_path):
    _write_config(tmp_path, api_port="not-a-port")
    with pytest.raises(RuntimeConfigError, match="api_port must be an integer"):
        resolve_runtime_config(cwd=tmp_path, environ={})


def test_memory_engine_defaults_use_canonical_config(tmp_path, monkeypatch):
    _write_config(
        tmp_path,
        database_path="engine.db",
        embedder_mode="hash",
        short_term_max=7,
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SQLITE_DB_PATH", raising=False)
    monkeypatch.delenv("EMBEDDER_MODE", raising=False)
    monkeypatch.delenv("SHORT_TERM_MAX", raising=False)

    from server.core.memory_engine import MemoryEngine

    engine = MemoryEngine()
    assert engine.db.db_path == str((tmp_path / "engine.db").resolve())
    assert engine._embedder_mode == "hash"
    assert engine.short_term.max_size == 7


def test_legacy_stackmemory_environment_falls_back(tmp_path):
    cfg = resolve_runtime_config(
        cwd=tmp_path,
        environ={"STACKMEMORY_API_PORT": "8123"},
    )
    assert cfg.api_port == 8123


def test_levh_environment_wins_over_legacy(tmp_path):
    cfg = resolve_runtime_config(
        cwd=tmp_path,
        environ={"LEVH_API_PORT": "8124", "STACKMEMORY_API_PORT": "8123"},
    )
    assert cfg.api_port == 8124
