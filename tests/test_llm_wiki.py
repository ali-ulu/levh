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
async def test_build_page_counters_match_the_registry_and_the_log(
    wiki_engine: MemoryEngine, tmp_path: Path
):
    """The page counters must partition the page registry and match the log.

    ``index.md`` is written outside the manifest ``pages`` registry, so folding
    its change into ``pages_changed`` used to report more changed pages than
    existed (5 changed against 4 pages on a first build). Every counter has to
    describe the same page set, and the append-only log has to agree with the
    API response for the same run.
    """
    await _seed(wiki_engine)
    root = tmp_path / "wiki"
    wiki = LLMWiki(wiki_engine, root=root)

    first = await wiki.build()
    assert first["pages_changed"] + first["pages_unchanged"] == first["pages"]
    assert first["pages_created"] == first["pages"]
    assert first["pages_changed"] == first["pages"]

    log_line = (root / "wiki" / "log.md").read_text(encoding="utf-8").strip().splitlines()[-1]
    assert f"pages changed: {first['pages_changed']};" in log_line
    assert f"{first['pages']} generated pages" in log_line

    # A rebuild that changes nothing must keep the invariant and report zero
    # changed pages, with the log agreeing again.
    second = await wiki.build()
    assert second["pages_changed"] + second["pages_unchanged"] == second["pages"]
    assert second["pages_changed"] == 0
    log_line = (root / "wiki" / "log.md").read_text(encoding="utf-8").strip().splitlines()[-1]
    assert f"pages changed: {second['pages_changed']};" in log_line


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


async def _seed_conflict_pair(engine: MemoryEngine) -> None:
    """Two memories sharing a person and org, with an opposing antonym signal."""
    await engine.store(
        content="Alice confirmed the Atlas launch is approved for Friday.",
        memory_type="episodic",
        source="local_files",
        project="Atlas",
        metadata={"attendees": ["alice@zephyrlabs.io"]},
    )
    await engine.store(
        content="Alice reported the Atlas launch is rejected by legal.",
        memory_type="episodic",
        source="slack",
        project="Atlas",
        metadata={"attendees": ["alice@zephyrlabs.io"]},
    )


@pytest.mark.asyncio
async def test_wiki_surfaces_open_conflict_candidates_it_did_not_detect(
    wiki_engine: MemoryEngine, tmp_path: Path
):
    """The Wiki reads conflict candidates LEVH already flagged; it never detects.

    Detection writes rows, and a build must stay a read of LEVH state. Here the
    detection pass runs first (as LEVH's own rebuild would), then the Wiki build
    must surface the candidate on the entity page, in wiki/conflicts.md and in
    the manifest — citing a snapshot for each memory — without creating any
    conflict rows itself.
    """
    await _seed_conflict_pair(wiki_engine)
    detected = await wiki_engine.conflict_service.detect_conflict_candidates()
    assert detected["new_candidates"] == 1
    before = await wiki_engine.conflict_service.list_conflict_candidates(status="open")
    assert len(before) == 1

    root = tmp_path / "wiki"
    wiki = LLMWiki(wiki_engine, root=root)
    result = await wiki.build()

    assert result["conflict_candidates"] == 1
    assert result["conflict_read_error"] is None
    assert wiki.status()["conflicts"] == 1

    conflict_page = (root / "wiki" / "conflicts.md").read_text(encoding="utf-8")
    assert "antonym" in conflict_page
    assert "not verdicts" in conflict_page

    # Every shared entity's page points back at the conflict, and the entity
    # page still keeps its own evidence section.
    conflict_entity_pages = [
        path for path in (root / "wiki" / "entities").rglob("*.md")
        if "## Conflict candidates — review required" in path.read_text(encoding="utf-8")
    ]
    assert conflict_entity_pages
    for path in conflict_entity_pages:
        assert "## Evidence" in path.read_text(encoding="utf-8")

    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert list(manifest["conflicts"]) == [before[0]["id"]]
    assert manifest["conflicts"][before[0]["id"]]["signal_type"] == "antonym"

    # A build reads conflicts; it must not add or mutate them.
    after = await wiki_engine.conflict_service.list_conflict_candidates(status="open")
    assert [row["id"] for row in after] == [row["id"] for row in before]

    lint = wiki.lint()
    assert lint["ok"] is True, json.dumps(lint["issues"], indent=2)


@pytest.mark.asyncio
async def test_lint_flags_a_conflict_whose_source_is_no_longer_indexed(
    wiki_engine: MemoryEngine, tmp_path: Path
):
    """A candidate that outlives a forgotten memory is a broken reference."""
    await _seed_conflict_pair(wiki_engine)
    await wiki_engine.conflict_service.detect_conflict_candidates()
    root = tmp_path / "wiki"
    wiki = LLMWiki(wiki_engine, root=root)
    await wiki.build()

    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    conflict_id = next(iter(manifest["conflicts"]))
    dropped = manifest["conflicts"][conflict_id]["memory_ids"][0]
    manifest["sources"].pop(dropped)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    codes = {issue["code"] for issue in wiki.lint()["issues"]}
    assert "conflict_source_missing" in codes


@pytest.mark.asyncio
async def test_lint_flags_a_registered_page_the_index_does_not_link(
    wiki_engine: MemoryEngine, tmp_path: Path
):
    """A page nothing links to from index.md is invisible to a reader."""
    await _seed(wiki_engine)
    root = tmp_path / "wiki"
    wiki = LLMWiki(wiki_engine, root=root)
    await wiki.build()
    assert wiki.lint()["ok"] is True

    # Drop one entity link from the index while its page stays registered: the
    # bytes are intact but a reader can no longer reach it from the entry point.
    index_path = root / "wiki" / "index.md"
    index_lines = index_path.read_text(encoding="utf-8").splitlines()
    removed = next(line for line in index_lines if line.startswith("- [") and "entities/" in line)
    index_path.write_text(
        "\n".join(line for line in index_lines if line != removed) + "\n",
        encoding="utf-8",
    )
    codes = {issue["code"] for issue in wiki.lint()["issues"]}
    assert "unreachable_page" in codes


@pytest.mark.asyncio
async def test_build_log_has_a_greppable_header_and_detail_line(
    wiki_engine: MemoryEngine, tmp_path: Path
):
    """The append-only log is parseable with plain unix tools, one entry per build."""
    await _seed(wiki_engine)
    root = tmp_path / "wiki"
    wiki = LLMWiki(wiki_engine, root=root)
    await wiki.build()

    log_text = (root / "wiki" / "log.md").read_text(encoding="utf-8")
    headers = [line for line in log_text.splitlines() if line.startswith("## [")]
    assert headers, log_text
    assert "build |" in headers[-1]
    # The counters stay on a single detail line so `grep` finds them.
    detail = [line for line in log_text.splitlines() if line.startswith("- build:")]
    assert detail
    assert "conflict candidates: 0;" in detail[-1]
