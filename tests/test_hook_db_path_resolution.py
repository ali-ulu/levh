"""Regression tests for issue #135: the hook/MCP install paths must resolve
the database path through ``get_env`` so the canonical ``LEVH_SQLITE_DB_PATH``
spelling is honoured. A bare ``os.getenv("SQLITE_DB_PATH")`` silently ignored
it and installed hooks/MCP clients pointing at an empty default store — the
memory never errors, it just goes missing.
"""

from __future__ import annotations

import json
import os

import pytest

from server.commands import hooks as hook_commands
from server.commands import universal_hooks
from server.core.db import schema as db_schema


SPELLING_CASES = [
    ("LEVH_SQLITE_DB_PATH", "/tmp/levh_store.db"),
    ("SQLITE_DB_PATH", "/tmp/bare_store.db"),
    ("STACKMEMORY_SQLITE_DB_PATH", "/tmp/legacy_store.db"),
]


@pytest.fixture(autouse=True)
def _clean_db_path_env(monkeypatch):
    for name in (
        "LEVH_SQLITE_DB_PATH",
        "SQLITE_DB_PATH",
        "STACKMEMORY_SQLITE_DB_PATH",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("name,path", SPELLING_CASES)
def test_hook_db_path_accepts_every_spelling(monkeypatch, name, path):
    """Every accepted spelling of the db-path setting reaches the SessionStart
    hook template — the resolution matches ``get_env`` exactly."""
    monkeypatch.setenv(name, path)
    assert hook_commands._resolved_db_path() == os.path.abspath(path)


@pytest.mark.parametrize("name,path", SPELLING_CASES)
def test_universal_hook_db_path_accepts_every_spelling(monkeypatch, name, path):
    """MCP install configs (env.SQLITE_DB_PATH) resolve the same way."""
    monkeypatch.setenv(name, path)
    assert universal_hooks._resolved_db_path() == os.path.abspath(path)


def test_canonical_spelling_wins_over_legacy(monkeypatch):
    """Precedence is get_env's: LEVH_ > bare > STACKMEMORY_."""
    monkeypatch.setenv("STACKMEMORY_SQLITE_DB_PATH", "/tmp/legacy.db")
    monkeypatch.setenv("SQLITE_DB_PATH", "/tmp/bare.db")
    monkeypatch.setenv("LEVH_SQLITE_DB_PATH", "/tmp/canonical.db")
    assert hook_commands._resolved_db_path() == os.path.abspath("/tmp/canonical.db")


def test_empty_value_falls_back_to_default(monkeypatch):
    """An unset or whitespace-only variable means 'no override' — the default
    store, never an empty path."""
    monkeypatch.setenv("LEVH_SQLITE_DB_PATH", "   ")
    resolved = hook_commands._resolved_db_path()
    assert resolved.endswith("stackmemory.db")
    assert os.path.isabs(resolved)


def test_no_variable_at_all_yields_absolute_default(monkeypatch):
    resolved = universal_hooks._resolved_db_path()
    assert resolved.endswith("stackmemory.db")
    assert os.path.isabs(resolved)


def test_schema_default_resolves_canonical_spelling(monkeypatch):
    """``Database()``'s fallback default honours the canonical spelling too —
    the schema module read ``os.getenv`` directly and had the same gap."""
    import importlib

    monkeypatch.setenv("LEVH_SQLITE_DB_PATH", "/tmp/schema_store.db")
    reloaded = importlib.reload(db_schema)
    try:
        assert reloaded._DEFAULT_DB_PATH == "/tmp/schema_store.db"
    finally:
        importlib.reload(db_schema)  # restore module state for other tests


def test_librarian_mcp_config_honours_canonical_spelling(
    monkeypatch, tmp_path
):
    """The librarian's generated MCP configs carry the user's actual store —
    that generator also read ``os.getenv`` directly and had the same gap."""
    import importlib

    from server.core.librarian import config as librarian_config

    reloaded = importlib.reload(librarian_config)
    monkeypatch.setenv("LEVH_SQLITE_DB_PATH", str(tmp_path / "real.db"))
    # Redirect Path.home() so the installer writes into the sandbox.
    monkeypatch.setattr(
        reloaded.Path, "home", classmethod(lambda cls: tmp_path)
    )
    monkeypatch.setattr(
        reloaded.shutil, "which", lambda _: str(tmp_path / "fake-levh")
    )
    result = reloaded.add_levh_mcp("opencode")
    assert result.get("ok") is True, result
    config_path = tmp_path / ".opencode" / "mcp.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    env = payload["mcpServers"]["levh"]["env"]
    assert env["SQLITE_DB_PATH"] == str(tmp_path / "real.db")
