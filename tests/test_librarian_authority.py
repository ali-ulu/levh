"""The librarian's authority boundary, and the config surface inside it.

The boundary is the point of this file. The librarian used to run whatever
PowerShell an LLM asked it to, behind a blocklist that (a) could not hold and
(b) was the wrong shape regardless: the endpoint that reaches it needs no
authentication by default, so "the model proposed a command" was enough to run
code on the machine. These tests pin the removal, not the blocklist — a
blocklist test would pass again the day someone re-adds the capability with a
better regex.

The rest covers the one write that remains — adding a levh MCP entry to an
agent's config — where the failure that matters is silent data loss in a file
the librarian did not write.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

os.environ["EMBEDDER_MODE"] = "hash"

from server.core import engine_provider, librarian  # noqa: E402
from server.core.memory_engine import MemoryEngine  # noqa: E402


@pytest_asyncio.fixture
async def engine_api():
    import server.api as api_mod

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    if api_mod._engine is not None:
        await api_mod._engine.shutdown()
    api_mod._engine = MemoryEngine(db_path=path, embedder_mode="hash", short_term_max=20)
    # Routes reach the engine through server.api; the librarian reaches it
    # through engine_provider. Going through api.get_engine() points both at
    # this temporary database — otherwise the librarian under test would write
    # findings into the operator's real store.
    await api_mod.get_engine()
    api_mod._initialized = True
    transport = ASGITransport(app=api_mod.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, api_mod._engine
    await api_mod._engine.shutdown()
    api_mod._engine = None
    api_mod._initialized = False
    engine_provider.set_engine(None)
    if os.path.exists(path):
        os.unlink(path)


# ── The boundary ─────────────────────────────────────────────────────


def test_the_module_exposes_no_command_execution():
    """Stated against the module surface, not against one function name, so
    re-adding the capability under any name fails here."""
    assert not hasattr(librarian, "run_shell")
    assert "shell" not in librarian._ALLOWED_ACTIONS

    source = Path(librarian.__file__).read_text(encoding="utf-8")
    for forbidden in ("subprocess.run", "subprocess.Popen", "os.system", "os.popen"):
        assert forbidden not in source, f"librarian regained a way to execute: {forbidden}"


def test_the_system_prompt_does_not_offer_a_shell_action():
    """The model only proposes what it was told exists."""
    prompt = librarian._SYSTEM_PROMPT
    assert '"shell"' not in prompt
    assert "TERMINAL KOMUTU" in prompt  # and is told plainly that it cannot


@pytest.mark.asyncio
async def test_a_shell_action_is_refused_and_reported(engine_api):
    """The refusal is not silent: attempting it is itself worth a finding."""
    client, _engine = engine_api
    result = await librarian.execute_action(
        {"type": "shell", "command": "Remove-Item -Recurse -Force ~"}
    )
    assert result["ok"] is False
    assert "izin verilmeyen" in result["msg"]

    findings = (await client.get("/api/findings")).json()["findings"]
    assert [f["category"] for f in findings] == ["agent"]
    assert findings[0]["severity"] == "high"
    # The rejected command is evidence, and evidence is scrubbed like any other.
    assert str(Path.home()) not in findings[0]["detail"]


@pytest.mark.asyncio
async def test_an_invented_action_type_is_refused(engine_api):
    result = await librarian.execute_action({"type": "exec_python", "code": "1"})
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_report_finding_is_the_way_an_action_reaches_the_human(engine_api):
    client, _engine = engine_api
    result = await librarian.execute_action({
        "type": "report_finding",
        "title": "codex config bozuk",
        "detail": "TOML ayrıştırılamadı",
        "category": "config",
        "severity": "high",
    })
    assert result["ok"] is True
    findings = (await client.get("/api/findings")).json()["findings"]
    assert findings[0]["title"] == "codex config bozuk"


@pytest.mark.asyncio
async def test_none_action_does_nothing(engine_api):
    client, _engine = engine_api
    assert (await librarian.execute_action({"type": "none"}))["ok"] is True
    assert (await client.get("/api/findings")).json()["findings"] == []


# ── The config write that remains ────────────────────────────────────


def test_backups_do_not_overwrite_each_other(tmp_path, monkeypatch):
    """A fixed .bak name meant the second bad write destroyed the only good
    copy — which is the moment a backup exists for."""
    cfg = tmp_path / "mcp.json"
    cfg.write_text('{"mcpServers": {}}', encoding="utf-8")

    first = librarian._backup(cfg)
    cfg.write_text('{"mcpServers": {"x": 1}}', encoding="utf-8")
    second = librarian._backup(cfg)

    assert first is not None and second is not None
    assert first != second
    assert first.exists() and second.exists()


def test_an_unreadable_claude_config_is_left_untouched(tmp_path, monkeypatch):
    """.claude.json carries session state and this code rewrites the whole
    file. Reading it with errors="ignore" and writing the result back would
    drop the unreadable bytes permanently."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    cfg = tmp_path / ".claude.json"
    original = b'{"projects": {"a": 1},, broken'
    cfg.write_bytes(original)

    result = librarian.add_levh_mcp("claude-code")

    assert result["ok"] is False
    assert cfg.read_bytes() == original  # not rewritten, not truncated


def test_adding_levh_preserves_the_rest_of_the_config(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    cfg = tmp_path / ".cline" / "mcp.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        json.dumps({"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}),
        encoding="utf-8",
    )

    result = librarian.add_levh_mcp("cline")

    assert result["ok"] is True
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert "levh" in data["mcpServers"]
    assert data["mcpServers"]["other"] == {"command": "x"}  # untouched
    assert data["theme"] == "dark"                          # untouched


def test_adding_levh_twice_is_a_no_op(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    cfg = tmp_path / ".cline" / "mcp.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('{"mcpServers": {}}', encoding="utf-8")

    librarian.add_levh_mcp("cline")
    before = cfg.read_text(encoding="utf-8")
    assert librarian.add_levh_mcp("cline")["ok"] is True
    assert cfg.read_text(encoding="utf-8") == before


def test_the_executable_is_resolved_not_hardcoded():
    """The fallback used to be one machine's absolute path, which produced a
    config that worked there and nowhere else. Stated against the source so it
    fails if any absolute home path is reintroduced."""
    import re

    source = Path(librarian.__file__).read_text(encoding="utf-8")
    hardcoded = re.findall(r"[A-Za-z]:\\\\Users\\\\[A-Za-z0-9_.-]+", source)
    assert not hardcoded, f"a specific machine's path is baked in: {hardcoded}"


def test_the_executable_fallback_points_at_this_interpreter(tmp_path, monkeypatch):
    """With levh off PATH, the fallback must still name something usable —
    the console script next to the running interpreter, or the bare name."""
    monkeypatch.setattr(librarian.shutil, "which", lambda _name: None)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    cfg = tmp_path / ".cline" / "mcp.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('{"mcpServers": {}}', encoding="utf-8")

    librarian.add_levh_mcp("cline")

    command = json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"]["levh"]["command"]
    assert command == "levh" or Path(command).exists()


def test_an_unknown_agent_is_reported_not_guessed():
    assert librarian.add_levh_mcp("no-such-agent")["ok"] is False
