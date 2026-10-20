"""Connector source identity/version contract.

These tests define what must happen when the same external object is observed
again. Connector imports are not a pile of unrelated notes: one source object
has one current revision, repeated revisions are idempotent, and older
revisions remain available for point-in-time/history reads.
"""

from __future__ import annotations

import os
import tempfile

import pytest
import pytest_asyncio

from server.connectors.github import GitHubConnector
from server.core.memory_engine import MemoryEngine


REPO = "ali-ulu/levh"
SOURCE_ID = f"github:{REPO}:issue:42"
REVISION_1 = "2026-10-20T10:00:00Z"
REVISION_2 = "2026-10-20T11:00:00Z"
CONTENT_1 = (
    "Issue 42: deploy production from branch blue and notify the operations owner "
    "before the scheduled maintenance window."
)
CONTENT_2 = (
    "Issue 42: deploy production from branch green and notify the release manager "
    "after the new rollout validation has completed."
)


@pytest_asyncio.fixture
async def engine(monkeypatch):
    monkeypatch.setenv("EMBEDDER_MODE", "hash")
    monkeypatch.setenv("LEVH_SUPERSESSION", "0")
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    eng = MemoryEngine(db_path=db_path, embedder_mode="hash", short_term_max=10)
    await eng.initialize()
    yield eng
    await eng.shutdown()
    if os.path.exists(db_path):
        os.unlink(db_path)


def _source_item(
    content: str,
    revision: str,
    *,
    state: str = "open",
) -> dict:
    return {
        "content": content,
        "tags": ["github", "issue", f"repo:{REPO}"],
        "metadata": {
            "source": "github",
            "type": "issue",
            "repo": REPO,
            "issue_number": 42,
            "source_identity": SOURCE_ID,
            "source_revision": revision,
            "state": state,
            "updated_at": revision,
        },
    }


@pytest.mark.asyncio
async def test_changed_source_revision_replaces_current_memory_and_preserves_history(engine):
    await engine.ingest_items(
        [_source_item(CONTENT_1, REVISION_1)],
        connector="github",
    )
    first_active = await engine.list_memories(
        source="connector:github", limit=100
    )
    assert len(first_active) == 1
    first_id = first_active[0].id

    await engine.ingest_items(
        [_source_item(CONTENT_2, REVISION_2)],
        connector="github",
    )

    current = await engine.list_memories(source="connector:github", limit=100)
    assert len(current) == 1
    assert current[0].content == CONTENT_2
    assert current[0].metadata["source_identity"] == SOURCE_ID
    assert current[0].metadata["source_revision"] == REVISION_2

    previous = await engine.get_memory(first_id)
    assert previous is not None
    assert previous.content == CONTENT_1
    assert previous.valid_to is not None
    assert previous.superseded_by == current[0].id

    history = await engine.list_memories(
        source="connector:github",
        include_superseded=True,
        limit=100,
    )
    assert {memory.id for memory in history} == {first_id, current[0].id}


@pytest.mark.asyncio
async def test_same_source_revision_is_idempotent(engine):
    item = _source_item(CONTENT_1, REVISION_1)
    first = await engine.ingest_items([item], connector="github")
    second = await engine.ingest_items([item], connector="github")

    current = await engine.list_memories(source="connector:github", limit=100)
    assert first["stored"] == 1
    assert second["stored"] == 0
    assert len(current) == 1
    assert current[0].metadata["source_revision"] == REVISION_1


@pytest.mark.asyncio
async def test_source_state_change_is_a_new_revision_even_when_body_is_unchanged(engine):
    await engine.ingest_items(
        [_source_item(CONTENT_1, REVISION_1, state="open")],
        connector="github",
    )
    await engine.ingest_items(
        [_source_item(CONTENT_1, REVISION_2, state="closed")],
        connector="github",
    )

    current = await engine.list_memories(source="connector:github", limit=100)
    assert len(current) == 1
    assert current[0].metadata["state"] == "closed"
    assert current[0].metadata["source_revision"] == REVISION_2


@pytest.mark.asyncio
async def test_stale_source_revision_cannot_replace_newer_current_memory(engine):
    await engine.ingest_items(
        [_source_item(CONTENT_2, REVISION_2, state="closed")],
        connector="github",
    )

    stale_result = await engine.ingest_items(
        [_source_item(CONTENT_1, REVISION_1, state="open")],
        connector="github",
    )

    current = await engine.list_memories(source="connector:github", limit=100)
    assert len(current) == 1
    assert current[0].content == CONTENT_2
    assert current[0].metadata["source_revision"] == REVISION_2
    assert current[0].metadata["state"] == "closed"
    assert stale_result["stored"] == 0
    assert stale_result["errors"] == 0


@pytest.mark.asyncio
async def test_github_issue_metadata_contains_stable_identity_and_revision():
    class Response:
        status_code = 200

        def json(self):
            return [
                {
                    "number": 42,
                    "title": "Source revision fixture",
                    "body": "Test body for external source identity.",
                    "labels": [{"name": "bug"}],
                    "html_url": f"https://github.com/{REPO}/issues/42",
                    "state": "open",
                    "created_at": "2026-10-20T09:00:00Z",
                    "updated_at": REVISION_2,
                }
            ]

    class Client:
        async def get(self, url, **kwargs):
            assert url.endswith(f"/repos/{REPO}/issues")
            assert kwargs["params"]["state"] == "all"
            return Response()

    connector = GitHubConnector()
    connector._headers = {"Authorization": "Bearer test-token"}
    items = await connector._fetch_issues(Client(), REPO)

    assert len(items) == 1
    metadata = items[0]["metadata"]
    assert metadata["source_identity"] == SOURCE_ID
    assert metadata["source_revision"] == REVISION_2
    assert metadata["updated_at"] == REVISION_2


@pytest.mark.asyncio
async def test_github_issue_fetch_failure_is_not_reported_as_empty_snapshot():
    class Response:
        status_code = 503

        def json(self):
            raise AssertionError("a failed response must not be parsed")

    class Client:
        async def get(self, url, **kwargs):
            return Response()

    connector = GitHubConnector()
    connector._headers = {"Authorization": "Bearer test-token"}

    with pytest.raises(ConnectionError, match="GitHub issue fetch failed"):
        await connector._fetch_issues(Client(), REPO)
