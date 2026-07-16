"""Canonical runtime configuration resolution for LEVH.

Precedence is intentionally uniform across CLI, API, MCP and background
providers:

    explicit arguments > process environment > .stackmemory/config.json > defaults

The resolver is side-effect free. It never writes config files, never loads a
``.env`` implicitly and never contacts the network.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Mapping

from server.core.env import get_env

CONFIG_DIR = ".stackmemory"
CONFIG_FILE = "config.json"
CONFIG_PATH_ENV = "LEVH_CONFIG_PATH"

DEFAULTS: dict[str, Any] = {
    "database_path": "stackmemory.db",
    "embedder_mode": "auto",
    "short_term_max": 50,
    "api_host": "127.0.0.1",
    "api_port": 8000,
    "mcp_transport": "stdio",
}

_ENV_TO_KEY = {
    "SQLITE_DB_PATH": "database_path",
    "EMBEDDER_MODE": "embedder_mode",
    "SHORT_TERM_MAX": "short_term_max",
    "API_HOST": "api_host",
    "API_PORT": "api_port",
    "MCP_TRANSPORT": "mcp_transport",
}


class RuntimeConfigError(ValueError):
    """Configuration exists but is malformed or contains invalid values."""


@dataclass(frozen=True)
class RuntimeConfig:
    database_path: str
    embedder_mode: str
    short_term_max: int
    api_host: str
    api_port: int
    mcp_transport: str
    config_path: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "database_path": self.database_path,
            "embedder_mode": self.embedder_mode,
            "short_term_max": self.short_term_max,
            "api_host": self.api_host,
            "api_port": self.api_port,
            "mcp_transport": self.mcp_transport,
            "config_path": self.config_path,
        }


def _config_path(*, cwd: str | os.PathLike[str] | None, environ: Mapping[str, str]) -> Path:
    explicit = (get_env(CONFIG_PATH_ENV, "", environ=environ) or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    root = Path(cwd or os.getcwd())
    return root / CONFIG_DIR / CONFIG_FILE


def load_config_file(
    *,
    cwd: str | os.PathLike[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], Path | None]:
    """Load the local config file when present.

    Missing config is normal. A present but malformed config fails clearly
    instead of silently falling back to another database.
    """
    env = os.environ if environ is None else environ
    path = _config_path(cwd=cwd, environ=env)
    if not path.exists():
        return {}, None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeConfigError(f"Invalid LEVH config at {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise RuntimeConfigError(f"Invalid LEVH config at {path}: root must be an object")
    return raw, path


def _coerce_int(name: str, value: Any, *, minimum: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeConfigError(f"{name} must be an integer, got {value!r}") from exc
    if result < minimum:
        raise RuntimeConfigError(f"{name} must be >= {minimum}, got {result}")
    return result


def _resolve_database_path(value: Any, *, cwd: Path) -> str:
    text = str(value or "").strip()
    if not text:
        raise RuntimeConfigError("database_path cannot be empty")
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = cwd / path
    return str(path.resolve(strict=False))


def resolve_runtime_config(
    *,
    explicit: Mapping[str, Any] | None = None,
    cwd: str | os.PathLike[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> RuntimeConfig:
    """Resolve the canonical runtime configuration.

    ``explicit`` uses config keys (``database_path``, ``embedder_mode``...) and
    only non-``None`` values override lower-precedence sources.
    """
    env = os.environ if environ is None else environ
    root = Path(cwd or os.getcwd()).resolve(strict=False)
    file_cfg, path = load_config_file(cwd=root, environ=env)

    merged: dict[str, Any] = dict(DEFAULTS)
    for key in DEFAULTS:
        if key in file_cfg and file_cfg[key] is not None:
            merged[key] = file_cfg[key]
    for env_name, key in _ENV_TO_KEY.items():
        value = get_env(env_name, None, environ=env)
        if value is not None and str(value).strip() != "":
            merged[key] = value
    if explicit:
        for key, value in explicit.items():
            if key in DEFAULTS and value is not None:
                merged[key] = value

    database_path = _resolve_database_path(merged["database_path"], cwd=root)
    embedder_mode = str(merged["embedder_mode"] or "").strip().lower()
    if not embedder_mode:
        raise RuntimeConfigError("embedder_mode cannot be empty")
    short_term_max = _coerce_int("short_term_max", merged["short_term_max"], minimum=1)
    api_host = str(merged["api_host"] or "").strip()
    if not api_host:
        raise RuntimeConfigError("api_host cannot be empty")
    api_port = _coerce_int("api_port", merged["api_port"], minimum=1)
    if api_port > 65535:
        raise RuntimeConfigError(f"api_port must be <= 65535, got {api_port}")
    mcp_transport = str(merged["mcp_transport"] or "").strip().lower()
    if not mcp_transport:
        raise RuntimeConfigError("mcp_transport cannot be empty")

    return RuntimeConfig(
        database_path=database_path,
        embedder_mode=embedder_mode,
        short_term_max=short_term_max,
        api_host=api_host,
        api_port=api_port,
        mcp_transport=mcp_transport,
        config_path=str(path.resolve(strict=False)) if path is not None else None,
    )


def runtime_env(config: RuntimeConfig) -> dict[str, str]:
    """Return the process environment values needed by MCP child processes."""
    return {
        "SQLITE_DB_PATH": config.database_path,
        "EMBEDDER_MODE": config.embedder_mode,
        "SHORT_TERM_MAX": str(config.short_term_max),
    }
