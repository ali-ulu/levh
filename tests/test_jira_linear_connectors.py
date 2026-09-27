"""Jira and Linear connectors — request shape, payload mapping, and auth.

Both connectors talk to a remote API, so the tests drive the *real* connector
code against a stub transport rather than a mock object: the request that would
have gone out is asserted, and the response is fed through the same parsing the
production path uses. Nothing here touches the network.

Embedder independence is deliberate — this file never constructs an engine, so
it runs the same under `EMBEDDER_MODE=hash` and under a real provider.
"""

import json

import httpx
import pytest

from server.connectors import get_connector, list_connectors
from server.connectors.jira import DEFAULT_JQL, JiraConnector
from server.connectors.linear import LinearConnector


# Captured before any test patches it: the factory below must build a *real*
# client, and looking the class up through `httpx` after patching would call the
# patch itself.
_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _client_with(handler) -> httpx.AsyncClient:
    """An AsyncClient whose every request is answered by `handler`."""
    return _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler))


def _patch(monkeypatch, handler) -> None:
    """Route every `httpx.AsyncClient(...)` in the connector under test to `handler`."""
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _client_with(handler))


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """Credentials must come from the config under test, not the environment."""
    for key in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN", "LINEAR_API_KEY"):
        monkeypatch.delenv(key, raising=False)


# ── registry ─────────────────────────────────────────────────────────


def test_both_connectors_are_registered():
    names = {c["name"] for c in list_connectors()}
    assert {"jira", "linear"} <= names
    assert get_connector("jira").name == "jira"
    assert get_connector("linear").name == "linear"


def test_required_config_keys_are_declared():
    assert set(get_connector("jira").required_config_keys()) == {"base_url", "email", "api_token"}
    assert set(get_connector("linear").required_config_keys()) == {"api_key"}


# ── Jira: auth ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_jira_connect_requires_all_credentials():
    conn = JiraConnector()
    with pytest.raises(ValueError) as exc:
        await conn.connect({"base_url": "https://x.atlassian.net"})
    # The message must name every missing key, or a user fixes them one at a time.
    message = str(exc.value)
    for key in ("email", "api_token"):
        assert key in message


@pytest.mark.asyncio
async def test_jira_connect_rejects_a_bad_token(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/api/3/myself"
        return httpx.Response(401, text="unauthorized")

    _patch(monkeypatch, handler)
    conn = JiraConnector()
    with pytest.raises(ConnectionError):
        await conn.connect(
            {"base_url": "https://x.atlassian.net", "email": "a@b.c", "api_token": "t"}
        )


@pytest.mark.asyncio
async def test_jira_connect_sends_basic_auth(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"displayName": "Test"})

    _patch(monkeypatch, handler)
    conn = JiraConnector()
    assert await conn.connect(
        {"base_url": "https://x.atlassian.net/", "email": "a@b.c", "api_token": "t"}
    )
    # base64("a@b.c:t")
    assert seen["auth"] == "Basic YUBiLmM6dA=="
    # A trailing slash in config must not become a double slash in the URL.
    assert conn._base_url == "https://x.atlassian.net"


# ── Jira: payload mapping ────────────────────────────────────────────

_ADF_DESCRIPTION = {
    "type": "doc",
    "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "Deploy is blocked."}]},
        {"type": "paragraph", "content": [{"type": "text", "text": "Waiting on review."}]},
    ],
}

_JIRA_ISSUE = {
    "key": "OPS-42",
    "fields": {
        "summary": "Deploy pipeline is blocked",
        "description": _ADF_DESCRIPTION,
        "status": {"name": "In Progress"},
        "priority": {"name": "High"},
        "issuetype": {"name": "Bug"},
        "assignee": {"displayName": "Ali Ulu"},
        "reporter": {"displayName": "Someone"},
        "labels": ["release", "infra"],
        "created": "2026-09-01T00:00:00.000+0000",
        "updated": "2026-09-02T00:00:00.000+0000",
    },
}


def test_jira_issue_maps_to_a_memory():
    memory = JiraConnector()._to_memory(_JIRA_ISSUE)

    # A memory is recalled by its opening words, so the key and summary lead.
    assert memory["content"].startswith("OPS-42: Deploy pipeline is blocked")
    assert "Status: In Progress" in memory["content"]
    assert "Deploy is blocked." in memory["content"]
    assert "Waiting on review." in memory["content"]

    assert memory["tags"][:2] == ["jira", "issue"]
    assert "release" in memory["tags"]
    assert "status:In Progress" in memory["tags"]

    meta = memory["metadata"]
    assert meta["issue_key"] == "OPS-42"
    assert meta["assignee"] == "Ali Ulu"
    assert meta["priority"] == "High"
    assert meta["type"] == "issue"


def test_jira_adf_description_flattens_to_text():
    text = JiraConnector._flatten_description(_ADF_DESCRIPTION)
    assert "Deploy is blocked." in text
    assert "Waiting on review." in text
    # No ADF scaffolding should survive into the memory text.
    assert "{" not in text and "content" not in text


def test_jira_plain_string_description_passes_through():
    assert JiraConnector._flatten_description("just text") == "just text"
    assert JiraConnector._flatten_description(None) == ""


def test_jira_issue_without_optional_fields_still_maps():
    memory = JiraConnector()._to_memory({"key": "OPS-1", "fields": {"summary": "Bare"}})
    assert memory["content"] == "OPS-1: Bare"
    assert memory["metadata"]["assignee"] == ""
    assert "jira" in memory["tags"]


# ── Jira: fetch ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_jira_fetch_pages_when_max_issues_exceeds_one_page(monkeypatch):
    """Pagination only engages above one API page (100), because the request
    size is capped at 100 — so the test has to ask for more than that."""

    def issue(i: int) -> dict:
        return {**_JIRA_ISSUE, "key": f"OPS-{i}"}

    pages = [
        {"issues": [issue(i) for i in range(100)], "total": 150},
        {"issues": [issue(i) for i in range(100, 150)], "total": 150},
    ]
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/rest/api/3/myself":
            return httpx.Response(200, json={})
        calls.append(int(request.url.params.get("startAt", "0")))
        return httpx.Response(200, json=pages[len(calls) - 1])

    _patch(monkeypatch, handler)
    conn = JiraConnector()
    await conn.connect(
        {
            "base_url": "https://x.atlassian.net",
            "email": "a@b.c",
            "api_token": "t",
            "max_issues": 150,
        }
    )
    memories = await conn.fetch()

    assert len(memories) == 150
    assert calls == [0, 100]
    # The second page's last issue must survive, not be swallowed by the cap.
    assert memories[-1]["metadata"]["issue_key"] == "OPS-149"


@pytest.mark.asyncio
async def test_jira_fetch_sends_the_configured_jql(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/rest/api/3/myself":
            return httpx.Response(200, json={})
        seen["jql"] = request.url.params.get("jql")
        return httpx.Response(200, json={"issues": [], "total": 0})

    _patch(monkeypatch, handler)
    conn = JiraConnector()
    await conn.connect(
        {
            "base_url": "https://x.atlassian.net",
            "email": "a@b.c",
            "api_token": "t",
            "jql": "project = OPS",
        }
    )
    await conn.fetch()
    assert seen["jql"] == "project = OPS"


@pytest.mark.asyncio
async def test_jira_fetch_defaults_to_a_bounded_jql(monkeypatch):
    """A connector run must not walk an entire Jira site by default."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/rest/api/3/myself":
            return httpx.Response(200, json={})
        seen["jql"] = request.url.params.get("jql")
        return httpx.Response(200, json={"issues": [], "total": 0})

    _patch(monkeypatch, handler)
    conn = JiraConnector()
    await conn.connect(
        {"base_url": "https://x.atlassian.net", "email": "a@b.c", "api_token": "t"}
    )
    await conn.fetch()
    assert seen["jql"] == DEFAULT_JQL


@pytest.mark.asyncio
async def test_jira_fetch_stops_at_max_issues(monkeypatch):
    """A full page larger than the cap is trimmed, and no second request goes out."""
    page = {"issues": [{**_JIRA_ISSUE, "key": f"OPS-{i}"} for i in range(100)], "total": 999}
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/rest/api/3/myself":
            return httpx.Response(200, json={})
        calls.append(int(request.url.params.get("startAt", "0")))
        return httpx.Response(200, json=page)

    _patch(monkeypatch, handler)
    conn = JiraConnector()
    await conn.connect(
        {
            "base_url": "https://x.atlassian.net",
            "email": "a@b.c",
            "api_token": "t",
            "max_issues": 60,
        }
    )
    memories = await conn.fetch()

    assert len(memories) == 60
    assert calls == [0]


@pytest.mark.asyncio
async def test_jira_fetch_ends_on_a_short_page(monkeypatch):
    """A page smaller than the requested size means there is nothing after it,
    so the connector must not ask for another."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/rest/api/3/myself":
            return httpx.Response(200, json={})
        calls.append(int(request.url.params.get("startAt", "0")))
        return httpx.Response(200, json={"issues": [_JIRA_ISSUE], "total": 999})

    _patch(monkeypatch, handler)
    conn = JiraConnector()
    await conn.connect(
        {"base_url": "https://x.atlassian.net", "email": "a@b.c", "api_token": "t"}
    )
    memories = await conn.fetch()

    assert len(memories) == 1
    assert calls == [0]


@pytest.mark.asyncio
async def test_jira_comments_are_appended_when_requested(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/rest/api/3/myself":
            return httpx.Response(200, json={})
        if request.url.path == "/rest/api/3/search/jql":
            return httpx.Response(200, json={"issues": [_JIRA_ISSUE], "total": 1})
        assert "OPS-42/comment" in request.url.path
        return httpx.Response(
            200,
            json={
                "comments": [
                    {
                        "author": {"displayName": "Reviewer"},
                        "body": {
                            "type": "doc",
                            "content": [
                                {"type": "paragraph", "content": [{"type": "text", "text": "LGTM"}]}
                            ],
                        },
                    }
                ]
            },
        )

    _patch(monkeypatch, handler)
    conn = JiraConnector()
    await conn.connect(
        {
            "base_url": "https://x.atlassian.net",
            "email": "a@b.c",
            "api_token": "t",
            "include_comments": True,
        }
    )
    memories = await conn.fetch()
    assert "Reviewer: LGTM" in memories[0]["content"]


# ── Linear: auth ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_linear_connect_requires_a_key():
    with pytest.raises(ValueError):
        await LinearConnector().connect({})


@pytest.mark.asyncio
async def test_linear_connect_rejects_graphql_errors(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": [{"message": "bad key"}]})

    _patch(monkeypatch, handler)
    with pytest.raises(ConnectionError):
        await LinearConnector().connect({"api_key": "lin_bad"})


@pytest.mark.asyncio
async def test_linear_connect_sends_the_key_as_the_authorization_header(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"data": {"viewer": {"id": "u1", "name": "Ali"}}})

    _patch(monkeypatch, handler)
    assert await LinearConnector().connect({"api_key": "lin_key"})
    assert seen["auth"] == "lin_key"


# ── Linear: payload mapping ──────────────────────────────────────────

_LINEAR_ISSUE = {
    "identifier": "ENG-7",
    "title": "Ship the i18n design issue",
    "description": "Write the design issue before any code.",
    "url": "https://linear.app/x/issue/ENG-7",
    "priorityLabel": "Urgent",
    "createdAt": "2026-09-01T00:00:00.000Z",
    "updatedAt": "2026-09-02T00:00:00.000Z",
    "state": {"name": "Todo"},
    "assignee": {"displayName": "Ali Ulu"},
    "team": {"id": "t1", "key": "ENG", "name": "Engineering"},
    "project": {"id": "p1", "name": "Memory"},
    "labels": {"nodes": [{"name": "design"}, {"name": "docs"}]},
}


def test_linear_issue_maps_to_a_memory():
    memory = LinearConnector()._to_memory(_LINEAR_ISSUE)

    assert memory["content"].startswith("ENG-7: Ship the i18n design issue")
    assert "Status: Todo" in memory["content"]
    assert "Team: Engineering" in memory["content"]
    assert "design" in memory["tags"]
    assert "team:ENG" in memory["tags"]

    meta = memory["metadata"]
    assert meta["identifier"] == "ENG-7"
    assert meta["state"] == "Todo"
    assert meta["priority"] == "Urgent"
    assert meta["url"] == "https://linear.app/x/issue/ENG-7"


def test_linear_issue_without_optional_fields_still_maps():
    memory = LinearConnector()._to_memory({"identifier": "ENG-1", "title": "Bare"})
    assert memory["content"] == "ENG-1: Bare"
    assert memory["metadata"]["assignee"] == ""
    assert "linear" in memory["tags"]


# ── Linear: fetch ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_linear_fetch_follows_the_cursor(monkeypatch):
    responses = [
        {
            "data": {
                "issues": {
                    "nodes": [_LINEAR_ISSUE],
                    "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
                }
            }
        },
        {
            "data": {
                "issues": {
                    "nodes": [{**_LINEAR_ISSUE, "identifier": "ENG-8"}],
                    "pageInfo": {"hasNextPage": False, "endCursor": "c2"},
                }
            }
        },
    ]
    cursors: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        if body.get("query", "").startswith("query { viewer"):
            return httpx.Response(200, json={"data": {"viewer": {"id": "u1"}}})
        cursors.append(body["variables"].get("after"))
        return httpx.Response(200, json=responses[len(cursors) - 1])

    _patch(monkeypatch, handler)
    conn = LinearConnector()
    await conn.connect({"api_key": "lin_key"})
    memories = await conn.fetch()

    assert [m["metadata"]["identifier"] for m in memories] == ["ENG-7", "ENG-8"]
    assert cursors == [None, "c1"]


@pytest.mark.asyncio
async def test_linear_fetch_translates_filters_into_the_graphql_filter(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        if body.get("query", "").startswith("query { viewer"):
            return httpx.Response(200, json={"data": {"viewer": {"id": "u1"}}})
        seen["filter"] = body["variables"].get("filter")
        return httpx.Response(
            200,
            json={"data": {"issues": {"nodes": [], "pageInfo": {"hasNextPage": False}}}},
        )

    _patch(monkeypatch, handler)
    conn = LinearConnector()
    await conn.connect(
        {"api_key": "lin_key", "team_ids": ["t1"], "project_ids": ["p1"]}
    )
    await conn.fetch()

    assert seen["filter"] == {
        "team": {"id": {"in": ["t1"]}},
        "project": {"id": {"in": ["p1"]}},
    }


@pytest.mark.asyncio
async def test_linear_fetch_without_filters_sends_no_filter(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        if body.get("query", "").startswith("query { viewer"):
            return httpx.Response(200, json={"data": {"viewer": {"id": "u1"}}})
        seen["has_filter"] = "filter" in body["variables"]
        return httpx.Response(
            200,
            json={"data": {"issues": {"nodes": [], "pageInfo": {"hasNextPage": False}}}},
        )

    _patch(monkeypatch, handler)
    conn = LinearConnector()
    await conn.connect({"api_key": "lin_key"})
    await conn.fetch()
    assert seen["has_filter"] is False


@pytest.mark.asyncio
async def test_linear_comments_are_appended_when_requested(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        query = body.get("query", "")
        if query.startswith("query { viewer"):
            return httpx.Response(200, json={"data": {"viewer": {"id": "u1"}}})
        if "Comments" in query:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "issue": {
                            "comments": {
                                "nodes": [{"body": "LGTM", "user": {"displayName": "Reviewer"}}]
                            }
                        }
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "data": {
                    "issues": {
                        "nodes": [_LINEAR_ISSUE],
                        "pageInfo": {"hasNextPage": False, "endCursor": "c1"},
                    }
                }
            },
        )

    _patch(monkeypatch, handler)
    conn = LinearConnector()
    await conn.connect({"api_key": "lin_key", "include_comments": True})
    memories = await conn.fetch()
    assert "Reviewer: LGTM" in memories[0]["content"]


@pytest.mark.asyncio
async def test_linear_fetch_stops_at_max_issues(monkeypatch):
    nodes = [{**_LINEAR_ISSUE, "identifier": f"ENG-{i}"} for i in range(50)]

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        if body.get("query", "").startswith("query { viewer"):
            return httpx.Response(200, json={"data": {"viewer": {"id": "u1"}}})
        return httpx.Response(
            200,
            json={
                "data": {
                    "issues": {
                        "nodes": nodes,
                        "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
                    }
                }
            },
        )

    _patch(monkeypatch, handler)
    conn = LinearConnector()
    await conn.connect({"api_key": "lin_key", "max_issues": 50})
    memories = await conn.fetch()
    assert len(memories) == 50
