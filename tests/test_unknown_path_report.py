"""Unknown-path reporting: name the path a 404 storm asks for (issue #520).

Something polled ``/health`` for months and every single request answered
404. The access log recorded the status but never the reason, so nobody
could see *what* was being asked for without counting lines by hand. The
middleware report names the path instead — rate-limited per path so the
poller cannot flood it, and scoped so the two legitimate 404 shapes (a
stale build asking for a replaced dashboard chunk, a route answering its
own 404 for a missing resource) do not drown it.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDER_MODE", "hash")
    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "levh.db"))
    monkeypatch.delenv("LEVH_TOKEN", raising=False)
    from server import api
    from server.core import engine_provider

    api._engine = None
    api._initialized = False
    engine_provider.set_engine(None)

    # The report's limiter and seen-counts are process-wide so a real poller
    # stays rate-limited across requests; the tests need a clean slate each
    # time instead of sharing whatever earlier tests accumulated.
    from server import middleware
    from server.core.rate_limit import SlidingWindowRateLimiter

    monkeypatch.setattr(
        middleware,
        "_unknown_path_limiter",
        SlidingWindowRateLimiter(
            middleware._UNKNOWN_PATH_LOG_LIMIT, middleware._UNKNOWN_PATH_LOG_WINDOW_SECONDS
        ),
    )
    monkeypatch.setattr(middleware, "_unknown_path_seen", {})

    with TestClient(api.app, client=("127.0.0.1", 51234)) as c:
        yield c

    api._engine = None
    api._initialized = False
    engine_provider.set_engine(None)


def _route_not_found_records(caplog):
    return [
        record
        for record in caplog.records
        if record.name == "levh.middleware"
        and getattr(record, "structured", {}).get("event") == "route.not_found"
    ]


def test_unknown_path_is_reported_by_name(client, caplog):
    """The whole point: the log says *which* path kept 404ing."""
    with caplog.at_level(logging.WARNING, logger="levh.middleware"):
        response = client.get("/monitor-poll-check")

    assert response.status_code == 404
    reports = _route_not_found_records(caplog)
    assert len(reports) == 1
    payload = reports[0].structured
    assert payload["path"] == "/monitor-poll-check"
    assert "/monitor-poll-check" in reports[0].getMessage()
    assert " /api/health" in reports[0].getMessage()  # points at the fix


def test_report_carries_the_request_id(client, caplog):
    """The report and the access log line describe the same request."""
    with caplog.at_level(logging.WARNING, logger="levh.middleware"):
        response = client.get("/correlate-me", headers={"X-Request-ID": "trace-520.1"})

    assert response.status_code == 404
    report = _route_not_found_records(caplog)[0]
    assert report.structured["request_id"] == "trace-520.1"
    assert response.headers["X-Request-ID"] == "trace-520.1"


def test_report_is_rate_limited_per_path_but_covers_every_path(client, caplog):
    """A once-a-second poller must not bury the log it made findable."""
    with caplog.at_level(logging.WARNING, logger="levh.middleware"):
        for _ in range(7):
            client.get("/chatty-poller")
        client.get("/another-poller")

    reports = _route_not_found_records(caplog)
    chatty = [r for r in reports if r.structured["path"] == "/chatty-poller"]
    other = [r for r in reports if r.structured["path"] == "/another-poller"]

    # Five a minute, each naming the count so growth stays visible.
    assert [r.structured["count"] for r in chatty] == [1, 2, 3, 4, 5]
    # A second path is not silenced by the first one's budget.
    assert [r.structured["count"] for r in other] == [1]


def test_dashboard_asset_misses_stay_in_the_access_log(client, caplog):
    """A stale build asking for a replaced chunk is expected, not a mystery."""
    with caplog.at_level(logging.WARNING, logger="levh.middleware"):
        response = client.get("/_next/static/chunks/replaced-by-a-newer-build.js")

    assert response.status_code == 404
    assert _route_not_found_records(caplog) == []


def test_a_missing_resource_is_not_an_unknown_path(client, caplog):
    """A route answering its own 404 (deleted memory) is a normal answer."""
    with caplog.at_level(logging.WARNING, logger="levh.middleware"):
        response = client.get("/api/memories/deleted-memory-id")

    assert response.status_code == 404
    assert _route_not_found_records(caplog) == []


def test_the_health_alias_is_never_reported(client, caplog):
    """The alias from issue #520 answers; the report must stay quiet."""
    with caplog.at_level(logging.WARNING, logger="levh.middleware"):
        response = client.get("/health")

    assert response.status_code == 200
    assert _route_not_found_records(caplog) == []
