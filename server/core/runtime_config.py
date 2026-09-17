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
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

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


def configured_bind_host(
    *,
    argv: Sequence[str] | None = None,
    cwd: str | os.PathLike[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Resolve the address this process is bound to.

    ``--host`` in argv wins because it is the only source that is *actually*
    obeyed by the serving process: ``levh serve --host 0.0.0.0`` and the
    Dockerfile's ``uvicorn --host 0.0.0.0`` both bind what argv says while the
    config still holds the ``127.0.0.1`` default. Reading config first made
    surfaces that describe the boundary — ``/api/health``, ``levh doctor`` —
    describe a server that was not running (issue #156).

    Falls back to env then config then the default, and tolerates a malformed
    config so a health check never fails over an unrelated setting.
    """
    host = _bind_host_from_argv(sys.argv if argv is None else argv)
    if host:
        return host
    try:
        return resolve_runtime_config(cwd=cwd, environ=environ).api_host
    except RuntimeConfigError:
        return DEFAULTS["api_host"]


def configured_api_port(
    *,
    argv: Sequence[str] | None = None,
    cwd: str | os.PathLike[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Resolve the port this process serves on.

    ``--port`` in argv wins for the same reason ``--host`` does in
    :func:`configured_bind_host`: only the serving process obeys it, while
    config may still hold the ``8000`` default. Reading config first made the
    ``levh doctor`` live probe miss a server started as
    ``levh serve --host 0.0.0.0 --port 9000`` and report a boundary that was
    not the one in force (issue #170).

    Falls back to env then config then the default, and tolerates a malformed
    argv port or config so a health check never fails over either.
    """
    port = _port_from_argv(sys.argv if argv is None else argv)
    if port is not None:
        return port
    try:
        return resolve_runtime_config(cwd=cwd, environ=environ).api_port
    except (RuntimeConfigError, ValueError):
        return DEFAULTS["api_port"]


def _bind_host_from_argv(argv: Sequence[str]) -> str | None:
    """The ``--host`` value in argv, for ``--host X`` and ``--host=X``."""
    for index, token in enumerate(argv):
        if token == "--host":
            if index + 1 < len(argv) and argv[index + 1].strip():
                return argv[index + 1].strip()
        elif token.startswith("--host="):
            value = token.partition("=")[2].strip()
            if value:
                return value
    return None


def _port_from_argv(argv: Sequence[str]) -> int | None:
    """The ``--port`` value in argv, for ``--port X`` and ``--port=X``.

    Returns ``None`` when absent or unusable (non-numeric, out of range) so the
    caller can fall back to env and config instead of failing the probe.
    """
    for index, token in enumerate(argv):
        if token == "--port":
            candidate = argv[index + 1] if index + 1 < len(argv) else ""
        elif token.startswith("--port="):
            candidate = token.partition("=")[2]
        else:
            continue
        return _parsed_port(candidate)
    return None


def _parsed_port(value: str) -> int | None:
    candidate = value.strip()
    if not candidate.isdigit():
        return None
    parsed = int(candidate)
    return parsed if 1 <= parsed <= 65535 else None
