"""Hardening contracts for the source-linked LLM Wiki (phases B–E).

``tests/test_llm_wiki.py`` locks the happy path. This module locks the edges
the plan calls out: filesystem containment, malformed manifests, concurrent
builds, LEVH update/forget semantics, REST/MCP authorization, and the optional
LLM draft boundary. Everything runs offline — the provider is mocked, never
contacted.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from mcp.server.fastmcp import FastMCP

from server.core.llm_wiki import LLMWiki, WikiError
from server.core.memory_engine import MemoryEngine
from server.core.tenancy import (
    AuthorizationError,
    Principal,
    bind_principal,
    reset_principal,
)
from server.tools.access import ToolAccessDenied
from server.tools.register import register_all_tools

AMBIENT_KEY = "sk-test-never-valid"


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
        content="Met with Alice Example about the Atlas launch plan.",
        memory_type="episodic",
        source="local_files",
        project="Atlas",
        metadata={
            "file_name": "runbook.md",
            "source_identity": "local:docs/runbook.md",
            "source_revision": "v1",
        },
    )
    second = await engine.store(
        content="Met with Alice Example again; Atlas launches after review.",
        memory_type="episodic",
        source="local_files",
        project="Atlas",
        metadata={
            "file_name": "launch-notes.md",
            "source_identity": "local:docs/launch-notes.md",
            "source_revision": "v2",
        },
    )
    return [first.id, second.id]


def _manifest(root: Path) -> dict:
    return json.loads((root / "manifest.json").read_text(encoding="utf-8"))


def _write_manifest(root: Path, payload: dict) -> None:
    (root / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")


# ── Phase B: manifest structure and filesystem containment ───────────


@pytest.mark.parametrize("field", ["sources", "snapshots", "pages", "llm_summaries"])
@pytest.mark.asyncio
async def test_malformed_manifest_field_is_a_clear_error_not_a_crash(
    wiki_engine: MemoryEngine, tmp_path: Path, field: str
):
    """A hand-edited manifest must fail closed with a WikiError, not an
    AttributeError/ValueError from deep inside a reader."""
    await _seed(wiki_engine)
    root = tmp_path / "wiki"
    wiki = LLMWiki(wiki_engine, root=root)
    await wiki.build()

    payload = _manifest(root)
    payload[field] = ["not", "a", "mapping"]
    _write_manifest(root, payload)

    for operation in (wiki.status, wiki.lint):
        with pytest.raises(WikiError, match=field):
            operation()
    with pytest.raises(WikiError, match=field):
        await wiki.build()


@pytest.mark.asyncio
async def test_manifest_with_a_malformed_record_is_rejected(
    wiki_engine: MemoryEngine, tmp_path: Path
):
    await _seed(wiki_engine)
    root = tmp_path / "wiki"
    wiki = LLMWiki(wiki_engine, root=root)
    await wiki.build()

    payload = _manifest(root)
    payload["pages"]["broken"] = "not a record"
    _write_manifest(root, payload)

    with pytest.raises(WikiError, match="pages"):
        wiki.lint()


@pytest.mark.asyncio
async def test_manifest_with_omitted_fields_is_accepted(
    wiki_engine: MemoryEngine, tmp_path: Path
):
    """A manifest that simply lacks an optional section must not be mistaken
    for a malformed one."""
    await _seed(wiki_engine)
    root = tmp_path / "wiki"
    wiki = LLMWiki(wiki_engine, root=root)
    await wiki.build()

    payload = _manifest(root)
    payload.pop("llm_summaries", None)
    payload.pop("pages", None)
    _write_manifest(root, payload)

    assert wiki.status()["ready"] is True


@pytest.mark.asyncio
async def test_unknown_manifest_version_is_preserved_and_rejected(
    wiki_engine: MemoryEngine, tmp_path: Path
):
    await _seed(wiki_engine)
    root = tmp_path / "wiki"
    wiki = LLMWiki(wiki_engine, root=root)
    await wiki.build()

    payload = _manifest(root)
    payload["version"] = 999
    _write_manifest(root, payload)

    with pytest.raises(WikiError, match="unknown or invalid version"):
        wiki.status()
    # The file the operator must inspect is left untouched.
    assert _manifest(root)["version"] == 999


@pytest.mark.asyncio
async def test_corrupt_manifest_json_is_reported(wiki_engine: MemoryEngine, tmp_path: Path):
    await _seed(wiki_engine)
    root = tmp_path / "wiki"
    wiki = LLMWiki(wiki_engine, root=root)
    await wiki.build()
    (root / "manifest.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(WikiError, match="Cannot read Wiki manifest"):
        wiki.lint()


@pytest.mark.asyncio
async def test_manifest_path_traversal_is_reported_by_lint(
    wiki_engine: MemoryEngine, tmp_path: Path
):
    await _seed(wiki_engine)
    root = tmp_path / "wiki"
    wiki = LLMWiki(wiki_engine, root=root)
    await wiki.build()

    payload = _manifest(root)
    payload["pages"]["evil"] = {
        "id": "evil", "kind": "entity", "title": "Evil",
        "path": "../../etc/passwd", "content_sha256": "x",
        "source_memory_ids": [], "summary_source_ids": [],
    }
    payload["snapshots"]["evil-snap"] = {
        "snapshot_id": "evil-snap", "memory_id": "m",
        "path": "../../etc/hosts", "file_sha256": "x",
    }
    _write_manifest(root, payload)

    codes = {issue["code"] for issue in wiki.lint()["issues"]}
    assert "invalid_page_path" in codes
    assert "invalid_snapshot_path" in codes


@pytest.mark.asyncio
async def test_build_refuses_to_write_through_a_symlinked_directory(
    wiki_engine: MemoryEngine, tmp_path: Path
):
    """A pre-existing symlink in the tree must not become a write-through."""
    await _seed(wiki_engine)
    root = tmp_path / "wiki"
    outside = tmp_path / "outside"
    outside.mkdir()
    root.mkdir()
    (root / "raw").symlink_to(outside, target_is_directory=True)

    wiki = LLMWiki(wiki_engine, root=root)
    with pytest.raises(WikiError, match="symlink"):
        await wiki.build()
    assert list(outside.rglob("*")) == []


@pytest.mark.asyncio
async def test_missing_snapshot_is_linted_and_rebuilt(
    wiki_engine: MemoryEngine, tmp_path: Path
):
    await _seed(wiki_engine)
    root = tmp_path / "wiki"
    wiki = LLMWiki(wiki_engine, root=root)
    await wiki.build()
    snapshot = next((root / "raw").rglob("*.md"))
    snapshot.unlink()

    codes = {issue["code"] for issue in wiki.lint()["issues"]}
    assert "missing_snapshot" in codes

    result = await wiki.build()
    assert result["snapshots_added_or_changed"] >= 1
    assert snapshot.exists()
    assert wiki.lint()["ok"] is True


@pytest.mark.asyncio
async def test_stale_entity_pages_are_removed_on_rebuild(
    wiki_engine: MemoryEngine, tmp_path: Path
):
    """An entity that disappears from LEVH must not leave an orphan page."""
    memory_ids = await _seed(wiki_engine)
    root = tmp_path / "wiki"
    wiki = LLMWiki(wiki_engine, root=root)
    await wiki.build()
    person_files = list((root / "wiki" / "entities" / "person").rglob("*.md"))
    assert person_files

    for memory_id in memory_ids:
        record = await wiki_engine.episodic.get(memory_id)
        record.content = "Atlas status update with no typed entity."
        record.project = "Atlas"
        await wiki_engine.episodic.update(record)

    result = await wiki.build()
    assert result["pages_removed"] >= 1
    remaining = {path.name for path in (root / "wiki" / "entities").rglob("*.md")}
    assert not (remaining & {path.name for path in person_files})


@pytest.mark.asyncio
async def test_concurrent_builds_do_not_race_one_root(
    wiki_engine: MemoryEngine, tmp_path: Path
):
    """Two builds for one root must serialize; the loser fails clearly and the
    manifest stays valid."""
    await _seed(wiki_engine)
    root = tmp_path / "wiki"
    first = LLMWiki(wiki_engine, root=root)
    second = LLMWiki(wiki_engine, root=root)

    results = await asyncio.gather(
        first.build(), second.build(), return_exceptions=True
    )
    errors = [r for r in results if isinstance(r, BaseException)]
    successes = [r for r in results if isinstance(r, dict)]
    assert successes, "at least one build must complete"
    assert all(isinstance(e, WikiError) for e in errors)
    assert first.lint()["ok"] is True


@pytest.mark.asyncio
async def test_failed_build_keeps_the_last_valid_manifest_and_is_rerunnable(
    wiki_engine: MemoryEngine, tmp_path: Path
):
    await _seed(wiki_engine)
    root = tmp_path / "wiki"
    wiki = LLMWiki(wiki_engine, root=root)
    await wiki.build()
    manifest_before = (root / "manifest.json").read_bytes()

    snapshot = next((root / "raw").rglob("*.md"))
    snapshot.write_text("tampered bytes", encoding="utf-8")
    with pytest.raises(WikiError, match="immutable snapshot"):
        await wiki.build()

    assert (root / "manifest.json").read_bytes() == manifest_before
    assert snapshot.read_text(encoding="utf-8") == "tampered bytes"

    snapshot.unlink()
    result = await wiki.build()
    assert result["ok"] is True
    assert wiki.lint()["ok"] is True


@pytest.mark.asyncio
async def test_metadata_derived_project_names_cannot_escape_the_root(
    wiki_engine: MemoryEngine, tmp_path: Path
):
    await wiki_engine.store(
        content="A note about the [Evil](http://x) project.",
        memory_type="episodic",
        source="local_files",
        project="../../evil\n[link](http://evil.example)",
    )
    root = tmp_path / "wiki"
    wiki = LLMWiki(wiki_engine, root=root)
    result = await wiki.build()
    assert result["ok"] is True

    # Every generated project page stays inside the wiki root.
    project_files = list((root / "wiki" / "projects").rglob("*.md"))
    assert project_files
    for path in project_files:
        path.resolve().relative_to(root.resolve())
    # No file leaked beside the wiki root through the traversal characters.
    assert not list(tmp_path.glob("evil*"))
    assert wiki.lint()["ok"] is True


# ── Phase C: LEVH data semantics ─────────────────────────────────────


@pytest.mark.asyncio
async def test_update_creates_a_new_snapshot_and_retains_the_prior_one(
    wiki_engine: MemoryEngine, tmp_path: Path
):
    memory_id = (await _seed(wiki_engine))[0]
    root = tmp_path / "wiki"
    wiki = LLMWiki(wiki_engine, root=root)
    await wiki.build()
    first_snapshots = set(_manifest(root)["snapshots"])

    record = await wiki_engine.episodic.get(memory_id)
    record.content = "Met with Alice Example about the revised Atlas plan."
    await wiki_engine.episodic.update(record)

    result = await wiki.build()
    assert result["snapshots_added_or_changed"] == 1
    snapshots = _manifest(root)["snapshots"]
    assert len(snapshots) == len(first_snapshots) + 1
    assert first_snapshots <= set(snapshots)
    # The manifest's source pointer moves to the new snapshot for that memory.
    source = _manifest(root)["sources"][memory_id]
    assert source["snapshot_id"] not in first_snapshots


@pytest.mark.asyncio
async def test_forget_removes_snapshots_catalog_and_pages(
    wiki_engine: MemoryEngine, tmp_path: Path
):
    memory_id = (await _seed(wiki_engine))[0]
    root = tmp_path / "wiki"
    wiki = LLMWiki(wiki_engine, root=root)
    await wiki.build()
    assert wiki_engine and memory_id

    await wiki_engine.forget(memory_id)
    result = await wiki.build()
    assert result["snapshots_removed_for_forgotten_memories"] >= 1

    payload = _manifest(root)
    assert memory_id not in payload["sources"]
    assert all(rec["memory_id"] != memory_id for rec in payload["snapshots"].values())
    catalog = (root / "wiki" / "sources.md").read_text(encoding="utf-8")
    assert memory_id not in catalog
    assert wiki.lint()["ok"] is True


@pytest.mark.asyncio
async def test_records_without_typed_entities_stay_in_the_source_catalog(
    wiki_engine: MemoryEngine, tmp_path: Path
):
    memory = await wiki_engine.store(
        content="ok",
        memory_type="episodic",
        source="local_files",
        project="Atlas",
    )
    root = tmp_path / "wiki"
    wiki = LLMWiki(wiki_engine, root=root)
    await wiki.build()

    catalog = (root / "wiki" / "sources.md").read_text(encoding="utf-8")
    assert memory.id in catalog
    assert _manifest(root)["sources"][memory.id]["snapshot_id"]


@pytest.mark.asyncio
async def test_every_manifest_source_points_at_its_current_snapshot(
    wiki_engine: MemoryEngine, tmp_path: Path
):
    await _seed(wiki_engine)
    root = tmp_path / "wiki"
    wiki = LLMWiki(wiki_engine, root=root)
    await wiki.build()

    payload = _manifest(root)
    for memory_id, source in payload["sources"].items():
        snapshot = payload["snapshots"][source["snapshot_id"]]
        assert snapshot["memory_id"] == memory_id
    assert wiki.lint()["ok"] is True


@pytest.mark.asyncio
async def test_generated_page_bytes_are_deterministic_across_rebuilds(
    wiki_engine: MemoryEngine, tmp_path: Path
):
    """Only the log and the manifest timestamp may move between builds."""
    await _seed(wiki_engine)
    root = tmp_path / "wiki"
    wiki = LLMWiki(wiki_engine, root=root)
    await wiki.build()

    def page_bytes() -> dict[str, bytes]:
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in (root / "wiki").rglob("*.md")
            if path.name != "log.md"
        }

    before = page_bytes()
    await wiki.build()
    assert page_bytes() == before


# ── Phase D: REST and MCP contract boundaries ────────────────────────


@pytest_asyncio.fixture
async def wiki_api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
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
async def test_read_paths_do_not_mutate_memories_or_wiki_files(wiki_api_client):
    client, engine = wiki_api_client
    await engine.store(
        content="Met with Alice Example about Northstar readiness.",
        memory_type="episodic",
        source="local_files",
        project="Northstar",
    )
    assert (await client.post("/api/v1/wiki/build", json={})).status_code == 200

    root = Path(engine.db.db_path).parent / "llm-wiki"
    manifest_path = next(root.rglob("manifest.json"))
    wiki_root = manifest_path.parent
    before_files = {
        path.relative_to(wiki_root).as_posix(): path.read_bytes()
        for path in wiki_root.rglob("*") if path.is_file()
    }
    before_stats = (await engine.get_stats()).total_memories

    assert (await client.get("/api/v1/wiki/status")).status_code == 200
    assert (await client.get("/api/v1/wiki/lint")).status_code == 200
    assert (
        await client.get("/api/v1/wiki/search", params={"q": "Alice"})
    ).status_code == 200
    page_id = (
        await client.get("/api/v1/wiki/search", params={"q": "Alice"})
    ).json()["results"][0]["id"]
    assert (await client.get(f"/api/v1/wiki/pages/{page_id}")).status_code == 200

    after_files = {
        path.relative_to(wiki_root).as_posix(): path.read_bytes()
        for path in wiki_root.rglob("*") if path.is_file()
    }
    assert after_files == before_files
    assert (await engine.get_stats()).total_memories == before_stats


@pytest.mark.asyncio
async def test_legacy_unversioned_route_serves_the_same_handler(wiki_api_client):
    client, engine = wiki_api_client
    await engine.store(
        content="Met with Alice Example about Northstar.", memory_type="episodic",
        source="local_files", project="Northstar",
    )
    assert (await client.post("/api/wiki/build", json={})).status_code == 200
    legacy = await client.get("/api/wiki/status")
    versioned = await client.get("/api/v1/wiki/status")
    assert legacy.status_code == versioned.status_code == 200
    assert legacy.json() == versioned.json()


@pytest.mark.asyncio
async def test_build_is_rejected_for_a_viewer_principal(wiki_engine: MemoryEngine, tmp_path: Path):
    await _seed(wiki_engine)
    token = bind_principal(
        Principal(id="viewer", workspace_id="default", role="viewer")
    )
    try:
        wiki = LLMWiki(wiki_engine, root=tmp_path / "wiki")
        with pytest.raises(AuthorizationError):
            await wiki.build()
        assert not (tmp_path / "wiki" / "manifest.json").exists()
    finally:
        reset_principal(token)


@pytest.mark.asyncio
async def test_missing_page_and_invalid_search_return_documented_shapes(wiki_api_client):
    client, engine = wiki_api_client
    await engine.store(
        content="Met with Alice Example about Northstar.", memory_type="episodic",
        source="local_files", project="Northstar",
    )
    await client.post("/api/v1/wiki/build", json={})

    missing = await client.get("/api/v1/wiki/pages/does-not-exist")
    assert missing.status_code == 404
    assert "detail" in missing.json()

    empty = await client.get("/api/v1/wiki/search", params={"q": "   "})
    assert empty.status_code == 422
    assert "detail" in empty.json()


@pytest.mark.asyncio
async def test_read_only_profile_cannot_build_even_by_hidden_name(wiki_engine: MemoryEngine):
    mcp = FastMCP("wiki-read-only")
    register_all_tools(mcp, wiki_engine, profile="read_only")

    with pytest.raises(ToolAccessDenied):
        await mcp.call_tool("build_knowledge_wiki", {"llm_summaries": False})


@pytest.mark.asyncio
async def test_read_only_profile_can_lint_and_search(wiki_engine: MemoryEngine):
    await _seed(wiki_engine)
    build_mcp = FastMCP("wiki-admin")
    register_all_tools(build_mcp, wiki_engine, profile="admin")
    await build_mcp.call_tool("build_knowledge_wiki", {"llm_summaries": False})

    mcp = FastMCP("wiki-read-only")
    register_all_tools(mcp, wiki_engine, profile="read_only")
    lint = await mcp.call_tool("lint_knowledge_wiki", {})
    assert lint is not None
    search = await mcp.call_tool("search_knowledge_wiki", {"query": "Alice"})
    assert search is not None


# ── Phase E: optional LLM drafts, fully mocked ───────────────────────


class _Provider:
    """A mocked OpenAI chat-completions endpoint; no socket is ever opened."""

    def __init__(self, *, content: str = "", status: int = 200):
        self.content = content
        self.status = status
        self.calls: list[str] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = self

        async def post(_self, url, **kwargs):
            provider.calls.append(str(url))
            if provider.status != 200:
                return httpx.Response(
                    provider.status, json={"error": "x"},
                    request=httpx.Request("POST", str(url)),
                )
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": provider.content}}]},
                request=httpx.Request("POST", str(url)),
            )

        monkeypatch.setattr(httpx.AsyncClient, "post", post)


async def _seed_two_entity_memories(engine: MemoryEngine) -> None:
    await engine.store(
        content="Met with Alice Example about Atlas launch readiness.",
        memory_type="episodic", source="local_files", project="Atlas",
    )
    await engine.store(
        content="Met with Alice Example again; Atlas review passed.",
        memory_type="episodic", source="local_files", project="Atlas",
    )


def _enable_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANSWER_MODE", "llm")
    monkeypatch.setenv("OPENAI_API_KEY", AMBIENT_KEY)


def _draft_text(root: Path) -> str | None:
    payload = _manifest(root)
    for page in payload["pages"].values():
        if page.get("summary_source_ids"):
            content = (root / page["path"]).read_text(encoding="utf-8")
            if "## LLM summary draft" in content:
                return content.split("## LLM summary draft")[1].split("## Evidence")[0]
    return None


@pytest.mark.asyncio
async def test_default_build_makes_zero_outbound_calls(
    wiki_engine: MemoryEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("ANSWER_MODE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    await _seed_two_entity_memories(wiki_engine)

    provider = _Provider(content="should never be used [1]")
    provider.install(monkeypatch)

    wiki = LLMWiki(wiki_engine, root=tmp_path / "wiki")
    result = await wiki.build()
    assert result["ok"] is True
    assert provider.calls == []


@pytest.mark.asyncio
async def test_opt_in_creates_a_cited_draft_and_caches_it(
    wiki_engine: MemoryEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _enable_llm(monkeypatch)
    await _seed_two_entity_memories(wiki_engine)
    provider = _Provider(content="Alice Example works on Atlas. [1] [2]")
    provider.install(monkeypatch)

    root = tmp_path / "wiki"
    wiki = LLMWiki(wiki_engine, root=root)
    first = await wiki.build(llm_summaries=True)
    assert first["llm_summaries_created"] >= 1
    assert len(provider.calls) == first["llm_summaries_created"]
    draft = _draft_text(root)
    assert draft is not None and "[1]" in draft

    # Unchanged sources must not trigger a duplicate provider call.
    provider.calls.clear()
    second = await wiki.build(llm_summaries=True)
    assert provider.calls == []
    assert second["llm_summaries_created"] == 0
    assert _draft_text(root) is not None


@pytest.mark.asyncio
async def test_source_change_invalidates_the_cached_draft(
    wiki_engine: MemoryEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _enable_llm(monkeypatch)
    await _seed_two_entity_memories(wiki_engine)
    provider = _Provider(content="Alice Example works on Atlas. [1] [2]")
    provider.install(monkeypatch)

    root = tmp_path / "wiki"
    wiki = LLMWiki(wiki_engine, root=root)
    await wiki.build(llm_summaries=True)
    assert provider.calls

    record = (await wiki_engine.episodic.search(limit=1, fetch_embeddings=False))[0]
    record.content = "Met with Alice Example about a rewritten Atlas plan."
    await wiki_engine.episodic.update(record)

    provider.calls.clear()
    result = await wiki.build(llm_summaries=True)
    assert provider.calls, "a changed source must invalidate the cached draft"
    assert result["llm_summaries_created"] >= 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        "",  # empty output
        "Alice Example works on Atlas without any citation.",  # uncited
        "Alice Example works on Atlas. [9]",  # citation out of range
        "x" * 13000 + " [1]",  # oversized
    ],
)
async def test_unusable_drafts_are_discarded_but_evidence_survives(
    wiki_engine: MemoryEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str
):
    _enable_llm(monkeypatch)
    await _seed_two_entity_memories(wiki_engine)
    provider = _Provider(content=content)
    provider.install(monkeypatch)

    root = tmp_path / "wiki"
    wiki = LLMWiki(wiki_engine, root=root)
    result = await wiki.build(llm_summaries=True)

    assert result["ok"] is True
    assert result["llm_summaries_created"] == 0
    assert result["llm_summary_failures"] >= 1
    assert _draft_text(root) is None
    # Evidence pages and their numbered sections are unaffected.
    assert wiki.lint()["ok"] is True


@pytest.mark.asyncio
async def test_provider_http_error_does_not_block_evidence(
    wiki_engine: MemoryEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _enable_llm(monkeypatch)
    await _seed_two_entity_memories(wiki_engine)
    provider = _Provider(status=500)
    provider.install(monkeypatch)

    wiki = LLMWiki(wiki_engine, root=tmp_path / "wiki")
    result = await wiki.build(llm_summaries=True)
    assert result["ok"] is True
    assert result["llm_summary_failures"] >= 1
    assert wiki.lint()["ok"] is True


@pytest.mark.asyncio
async def test_draft_output_is_sanitized_before_it_is_written(
    wiki_engine: MemoryEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Raw HTML, external links and credential strings must not survive."""
    _enable_llm(monkeypatch)
    await _seed_two_entity_memories(wiki_engine)
    provider = _Provider(
        content=(
            "<b>Alice</b> Example on Atlas; see "
            "[site](https://evil.example/x); key "
            "sk-proj-12345678901234567890. [1]"
        )
    )
    provider.install(monkeypatch)

    root = tmp_path / "wiki"
    wiki = LLMWiki(wiki_engine, root=root)
    result = await wiki.build(llm_summaries=True)
    assert result["llm_summaries_created"] >= 1

    draft = _draft_text(root)
    assert draft is not None
    assert "<b>" not in draft
    assert "https://evil.example" not in draft
    assert "sk-proj-12345678901234567890" not in draft
    assert "Alice" in draft


@pytest.mark.asyncio
async def test_offline_policy_denies_opt_in_without_the_env_switch(
    wiki_engine: MemoryEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("ANSWER_MODE", "extractive")
    monkeypatch.setenv("OPENAI_API_KEY", AMBIENT_KEY)
    await _seed_two_entity_memories(wiki_engine)
    provider = _Provider(content="Alice Example works on Atlas. [1]")
    provider.install(monkeypatch)

    wiki = LLMWiki(wiki_engine, root=tmp_path / "wiki")
    with pytest.raises(WikiError, match="ANSWER_MODE=llm"):
        await wiki.build(llm_summaries=True)
    assert provider.calls == []
