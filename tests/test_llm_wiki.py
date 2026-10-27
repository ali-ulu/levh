"""Contract tests for the source-linked LLM Wiki projection."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio

from server.core.llm_wiki import LLMWiki, WikiError
from server.core.memory_engine import MemoryEngine


@pytest_asyncio.fixture
async def wiki_engine(tmp_path: Path):
    engine = MemoryEngine(
        db_path=str(tmp_path / "memories.db"),
        embedder_mode="hash",
        short_term_max=10,
    )
    await engine.initialize()
    yield engine
    await engine.shutdown()


async def _seed(engine: MemoryEngine) -> list[str]:
    first = await engine.store(
        content=(
            "Met with Alice Example about the Atlas launch plan. "
            "api_key=sk-proj-12345678901234567890"
        ),
        memory_type="episodic",
        source="local_files",
        project="Atlas",
        metadata={
            "file_name": "runbook.md",
            "relative_path": "docs/runbook.md",
            "source_identity": "local:docs/runbook.md",
            "source_revision": "v1",
            "url": "https://example.com/docs/runbook.md?api_key=do-not-copy",
        },
    )
    second = await engine.store(
        content=(
            "Met with Alice Example again; we agreed that Atlas launches "
            "after the reliability review."
        ),
        memory_type="episodic",
        source="local_files",
        project="Atlas",
        metadata={
            "file_name": "launch-notes.md",
            "relative_path": "docs/launch-notes.md",
            "source_identity": "local:docs/launch-notes.md",
            "source_revision": "v2",
        },
    )
    return [first.id, second.id]


@pytest.mark.asyncio
async def test_build_creates_raw_snapshots_and_linked_evidence_pages(
    wiki_engine: MemoryEngine, tmp_path: Path
):
    await _seed(wiki_engine)
    wiki = LLMWiki(wiki_engine, root=tmp_path / "wiki")
    result = await wiki.build()

    assert result["ok"] is True
    assert result["memories"] == 2
    assert result["snapshots_current"] == 2
    assert result["snapshots_added_or_changed"] == 2
    assert result["pages"] >= 4  # source catalog, project hub and typed entities
    assert (tmp_path / "wiki" / "AGENTS.md").is_file()
    assert (tmp_path / "wiki" / "schema.md").is_file()
    assert (tmp_path / "wiki" / "wiki" / "index.md").is_file()
    assert (tmp_path / "wiki" / "wiki" / "sources.md").is_file()
    assert (tmp_path / "wiki" / "wiki" / "log.md").is_file()

    raw_text = "\n".join(path.read_text(encoding="utf-8") for path in (tmp_path / "wiki" / "raw").rglob("*.md"))
    assert "sk-proj-12345678901234567890" not in raw_text
    assert "do-not-copy" not in raw_text
    assert "api_key=[REDACTED]" in raw_text

    lint = wiki.lint()
    assert lint["ok"] is True, json.dumps(lint["issues"], indent=2)
    found = wiki.search("Alice Example")
    assert found["count"] >= 1
    page = wiki.get_page(found["results"][0]["id"])
    assert page is not None and "## Evidence" in page["content"]


@pytest.mark.asyncio
async def test_lint_ignores_markdown_links_embedded_in_source_excerpts(
    wiki_engine: MemoryEngine, tmp_path: Path
):
    await wiki_engine.store(
        content=(
            "Reference [upstream](../README.md) and a malformed source link "
            "[that spans\nlines](missing.md)."
        ),
        memory_type="episodic",
        source="local_files",
        project="Atlas",
    )
    wiki = LLMWiki(wiki_engine, root=tmp_path / "wiki")
    await wiki.build()

    result = wiki.lint()
    assert result["ok"] is True, json.dumps(result["issues"], indent=2)
    generated_pages = list((tmp_path / "wiki" / "wiki").rglob("*.md"))
    assert any(r"\[upstream\]" in page.read_text(encoding="utf-8") for page in generated_pages)


@pytest.mark.asyncio
async def test_build_is_idempotent_and_does_not_rewrite_identical_pages(
    wiki_engine: MemoryEngine, tmp_path: Path
):
    await _seed(wiki_engine)
    wiki = LLMWiki(wiki_engine, root=tmp_path / "wiki")
    first = await wiki.build()
    second = await wiki.build()

    assert first["snapshots_added_or_changed"] == 2
    assert second["snapshots_added_or_changed"] == 0
    assert second["pages_changed"] == 0
    assert second["pages_unchanged"] == second["pages"]


@pytest.mark.asyncio
async def test_lint_detects_snapshot_tampering(wiki_engine: MemoryEngine, tmp_path: Path):
    await _seed(wiki_engine)
    wiki = LLMWiki(wiki_engine, root=tmp_path / "wiki")
    await wiki.build()
    snapshot = next((tmp_path / "wiki" / "raw").rglob("*.md"))
    snapshot.write_text(
        snapshot.read_text(encoding="utf-8") + "\nTampered evidence.\n",
        encoding="utf-8",
    )

    result = wiki.lint()
    assert result["ok"] is False
    assert any(issue["code"] == "snapshot_digest_mismatch" for issue in result["issues"])

    # A later build must not silently rewrite the evidence that lint just found
    # tampered at the same digest path.
    with pytest.raises(WikiError, match="Refusing to overwrite an existing immutable snapshot"):
        await wiki.build()
    assert "Tampered evidence." in snapshot.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_remote_llm_drafts_require_existing_explicit_opt_in(
    wiki_engine: MemoryEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("ANSWER_MODE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    wiki = LLMWiki(wiki_engine, root=tmp_path / "wiki")

    with pytest.raises(WikiError, match="ANSWER_MODE=llm"):
        await wiki.build(llm_summaries=True)
    assert not (tmp_path / "wiki" / "manifest.json").exists()



@pytest_asyncio.fixture
async def wiki_api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Exercise the real versioned router with a private SQLite database."""
    from httpx import ASGITransport, AsyncClient

    import server.api as api_mod
    from server.core import engine_provider

    monkeypatch.setenv("LEVH_EMBEDDER_MODE", "hash")
    monkeypatch.setenv("EMBEDDER_MODE", "hash")
    monkeypatch.setenv("LEVH_LIBRARIAN", "0")
    monkeypatch.delenv("LEVH_TOKEN", raising=False)

    previous = api_mod._engine
    if previous is not None:
        await previous.shutdown()
    engine = MemoryEngine(
        db_path=str(tmp_path / "wiki-api.db"),
        embedder_mode="hash",
        short_term_max=10,
    )
    await engine.initialize()
    api_mod._engine = engine
    api_mod._initialized = True
    engine_provider.set_engine(engine)

    transport = ASGITransport(app=api_mod.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, engine

    await engine.shutdown()
    api_mod._engine = None
    api_mod._initialized = False
    engine_provider.set_engine(None)


@pytest.mark.asyncio
async def test_versioned_wiki_routes_build_search_and_lint(wiki_api_client):
    client, engine = wiki_api_client
    await engine.store(
        content="Met with Alice Example about Northstar launch readiness.",
        memory_type="episodic",
        source="local_files",
        project="Northstar",
        metadata={"file_name": "northstar-notes.md", "source_identity": "local:northstar"},
    )

    before = await client.get("/api/v1/wiki/status")
    assert before.status_code == 200
    assert before.json()["ready"] is False

    built = await client.post("/api/v1/wiki/build", json={})
    assert built.status_code == 200, built.text
    assert built.json()["memories"] == 1

    status = await client.get("/api/v1/wiki/status")
    assert status.status_code == 200
    assert status.json()["ready"] is True

    lint = await client.get("/api/v1/wiki/lint")
    assert lint.status_code == 200
    assert lint.json()["ok"] is True, lint.text

    search = await client.get("/api/v1/wiki/search", params={"q": "Alice Example"})
    assert search.status_code == 200
    assert search.json()["count"] >= 1
    page_id = search.json()["results"][0]["id"]
    page = await client.get(f"/api/v1/wiki/pages/{page_id}")
    assert page.status_code == 200
    assert "## Evidence" in page.json()["content"]


@pytest.mark.asyncio
async def test_versioned_wiki_route_keeps_remote_summaries_opt_in(wiki_api_client, monkeypatch):
    client, engine = wiki_api_client
    await engine.store(
        content="Met with Bob Example about the Polaris roadmap.",
        memory_type="episodic",
        source="local_files",
        project="Polaris",
    )
    monkeypatch.delenv("ANSWER_MODE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    response = await client.post("/api/v1/wiki/build", json={"llm_summaries": True})
    assert response.status_code == 409
    assert not list((Path(engine.db.db_path).parent / "llm-wiki").rglob("manifest.json"))


@pytest.mark.asyncio
async def test_manifest_cannot_be_reused_across_workspaces(
    wiki_engine: MemoryEngine, tmp_path: Path
):
    from server.core.tenancy import Principal, bind_principal, reset_principal

    await _seed(wiki_engine)
    root = tmp_path / "wiki"
    await LLMWiki(wiki_engine, root=root).build()

    token = bind_principal(Principal(id="other", workspace_id="other", role="admin"))
    try:
        with pytest.raises(WikiError, match="different workspace"):
            LLMWiki(wiki_engine, root=root).status()
    finally:
        reset_principal(token)
