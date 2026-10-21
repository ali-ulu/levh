"""GitHub connector fetch-path contract.

Every failure mode of the issues/pulls fetchers must be observable as a
failure, never as a valid empty snapshot: an empty list would let a later
reconciliation pass retire source memories that were never actually seen.
The pagination and identity rules are covered here as well, because they are
what makes a repeated sync idempotent.
"""

from __future__ import annotations

import pytest

from server.connectors.github import GitHubConnector


REPO = "ali-ulu/levh"


class _StubResponse:
    """Minimal httpx-shaped response for the connector fetch paths."""

    def __init__(self, status_code: int = 200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _StubClient:
    """Client that returns queued responses and records the requested pages."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def get(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if not self._responses:
            raise AssertionError("the connector requested more pages than the fixture provides")
        return self._responses.pop(0)


def _issue(number: int | None, **overrides) -> dict:
    payload = {
        "number": number,
        "title": f"Issue {number}",
        "body": f"Body of issue {number}",
        "labels": [{"name": "bug"}],
        "html_url": f"https://github.com/{REPO}/issues/{number}",
        "state": "open",
        "created_at": "2026-10-20T09:00:00Z",
        "updated_at": "2026-10-20T10:00:00Z",
    }
    payload.update(overrides)
    return payload


def _pull_request(number: int | None, **overrides) -> dict:
    payload = {
        "number": number,
        "title": f"PR {number}",
        "body": f"Body of pull request {number}",
        "labels": [{"name": "enhancement"}],
        "html_url": f"https://github.com/{REPO}/pull/{number}",
        "state": "open",
        "user": {"login": "octocat"},
        "created_at": "2026-10-20T09:00:00Z",
        "updated_at": "2026-10-20T11:00:00Z",
    }
    payload.update(overrides)
    return payload


def _connector(max_issues: int = 50, max_prs: int = 20) -> GitHubConnector:
    connector = GitHubConnector()
    connector._headers = {"Authorization": "Bearer test-token"}
    connector._max_issues = max_issues
    connector._max_prs = max_prs
    return connector


def _failing_client(error: Exception):
    class Client:
        async def get(self, url, **kwargs):
            raise error

    return Client()


@pytest.mark.asyncio
async def test_issue_fetch_transport_error_raises_connection_error():
    import httpx

    client = _failing_client(httpx.ConnectError("connection refused"))

    with pytest.raises(ConnectionError, match="GitHub issue fetch failed"):
        await _connector()._fetch_issues(client, REPO)


@pytest.mark.asyncio
async def test_issue_fetch_failure_status_raises_connection_error():
    client = _StubClient([_StubResponse(503, None)])

    with pytest.raises(ConnectionError, match="GitHub issue fetch failed"):
        await _connector()._fetch_issues(client, REPO)


@pytest.mark.asyncio
async def test_issue_fetch_rejects_non_list_payload():
    client = _StubClient([_StubResponse(200, {"message": "not a list"})])

    with pytest.raises(ConnectionError, match="expected a list response"):
        await _connector()._fetch_issues(client, REPO)


@pytest.mark.asyncio
async def test_issue_fetch_rejects_malformed_issue_record():
    client = _StubClient([_StubResponse(200, ["not-a-dict"])])

    with pytest.raises(ConnectionError, match="malformed issue record"):
        await _connector()._fetch_issues(client, REPO)


@pytest.mark.asyncio
async def test_issue_fetch_skips_pull_requests_and_keeps_identity():
    client = _StubClient(
        [
            _StubResponse(
                200,
                [
                    {"number": 7, "title": "PR", "pull_request": {"url": "x"}},
                    _issue(42),
                ],
            )
        ]
    )

    items = await _connector()._fetch_issues(client, REPO)

    assert len(items) == 1
    metadata = items[0]["metadata"]
    assert metadata["issue_number"] == 42
    assert metadata["source_identity"] == f"github:{REPO}:issue:42"
    assert metadata["source_revision"] == "2026-10-20T10:00:00Z"


@pytest.mark.asyncio
async def test_issue_fetch_truncates_long_body():
    client = _StubClient([_StubResponse(200, [_issue(42, body="x" * 5000)])])

    items = await _connector()._fetch_issues(client, REPO)

    assert items[0]["content"].endswith("... (truncated)")


@pytest.mark.asyncio
async def test_issue_fetch_paginates_until_a_short_page():
    # The connector asks for at most 100 records per page, so a second page is
    # only requested when the first one came back full.
    first = [_issue(number) for number in range(1, 101)]
    second = [_issue(number) for number in range(101, 111)]
    client = _StubClient([_StubResponse(200, first), _StubResponse(200, second)])

    connector = _connector(max_issues=150)
    items = await connector._fetch_issues(client, REPO)

    # The second page is short, so the connector stops instead of asking for a
    # third page.
    assert len(items) == 110
    assert len(client.calls) == 2
    assert client.calls[0]["params"]["page"] == 1
    assert client.calls[0]["params"]["per_page"] == 100
    assert client.calls[1]["params"]["page"] == 2
    assert client.calls[0]["params"]["state"] == "all"


@pytest.mark.asyncio
async def test_issue_fetch_stops_at_the_configured_limit():
    # Every page is full, so only the limit can end the loop.
    pages = [_StubResponse(200, [_issue(number) for number in range(1, 4)]) for _ in range(5)]
    client = _StubClient(pages)

    connector = _connector(max_issues=3)
    items = await connector._fetch_issues(client, REPO)

    assert len(items) == 3
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_issue_fetch_without_number_omits_identity():
    client = _StubClient([_StubResponse(200, [_issue(None)])])

    items = await _connector()._fetch_issues(client, REPO)

    metadata = items[0]["metadata"]
    assert "source_identity" not in metadata
    assert metadata["issue_number"] is None
    assert items[0]["content"].startswith("Issue #?: ")


@pytest.mark.asyncio
async def test_issue_fetch_without_updated_at_omits_revision():
    client = _StubClient([_StubResponse(200, [_issue(42, updated_at="")])])

    items = await _connector()._fetch_issues(client, REPO)

    metadata = items[0]["metadata"]
    assert metadata["source_identity"] == f"github:{REPO}:issue:42"
    assert "source_revision" not in metadata


@pytest.mark.asyncio
async def test_pull_request_fetch_transport_error_raises_connection_error():
    import httpx

    client = _failing_client(httpx.ConnectError("connection refused"))

    with pytest.raises(ConnectionError, match="GitHub pull request fetch failed"):
        await _connector()._fetch_prs(client, REPO)


@pytest.mark.asyncio
async def test_pull_request_fetch_failure_status_raises_connection_error():
    client = _StubClient([_StubResponse(500, None)])

    with pytest.raises(ConnectionError, match="GitHub pull request fetch failed"):
        await _connector()._fetch_prs(client, REPO)


@pytest.mark.asyncio
async def test_pull_request_fetch_rejects_non_list_payload():
    client = _StubClient([_StubResponse(200, {"message": "not a list"})])

    with pytest.raises(ConnectionError, match="expected a list response"):
        await _connector()._fetch_prs(client, REPO)


@pytest.mark.asyncio
async def test_pull_request_fetch_rejects_malformed_record():
    client = _StubClient([_StubResponse(200, ["not-a-dict"])])

    with pytest.raises(ConnectionError, match="malformed record"):
        await _connector()._fetch_prs(client, REPO)


@pytest.mark.asyncio
async def test_pull_request_fetch_builds_identity_and_revision():
    client = _StubClient([_StubResponse(200, [_pull_request(5)])])

    items = await _connector()._fetch_prs(client, REPO)

    metadata = items[0]["metadata"]
    assert metadata["source_identity"] == f"github:{REPO}:pull_request:5"
    assert metadata["source_revision"] == "2026-10-20T11:00:00Z"
    assert metadata["author"] == "octocat"
    assert metadata["state"] == "open"


@pytest.mark.asyncio
async def test_pull_request_fetch_truncates_long_body():
    client = _StubClient([_StubResponse(200, [_pull_request(5, body="y" * 5000)])])

    items = await _connector()._fetch_prs(client, REPO)

    assert items[0]["content"].endswith("... (truncated)")


@pytest.mark.asyncio
async def test_pull_request_fetch_paginates_until_a_short_page():
    first = [_pull_request(number) for number in range(1, 101)]
    second = [_pull_request(number) for number in range(101, 106)]
    client = _StubClient([_StubResponse(200, first), _StubResponse(200, second)])

    connector = _connector(max_prs=150)
    items = await connector._fetch_prs(client, REPO)

    assert len(items) == 105
    assert len(client.calls) == 2
    assert client.calls[0]["params"]["per_page"] == 100
    assert client.calls[1]["params"]["page"] == 2


@pytest.mark.asyncio
async def test_pull_request_fetch_stops_at_the_configured_limit():
    pages = [_StubResponse(200, [_pull_request(number) for number in range(1, 4)]) for _ in range(5)]
    client = _StubClient(pages)

    connector = _connector(max_prs=3)
    items = await connector._fetch_prs(client, REPO)

    assert len(items) == 3
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_pull_request_fetch_without_number_omits_identity():
    client = _StubClient([_StubResponse(200, [_pull_request(None)])])

    items = await _connector()._fetch_prs(client, REPO)

    metadata = items[0]["metadata"]
    assert "source_identity" not in metadata
    assert metadata["pr_number"] is None
    assert items[0]["content"].startswith("PR #?: ")


@pytest.mark.asyncio
async def test_pull_request_fetch_without_updated_at_omits_revision():
    client = _StubClient([_StubResponse(200, [_pull_request(5, updated_at="")])])

    items = await _connector()._fetch_prs(client, REPO)

    metadata = items[0]["metadata"]
    assert metadata["source_identity"] == f"github:{REPO}:pull_request:5"
    assert "source_revision" not in metadata
