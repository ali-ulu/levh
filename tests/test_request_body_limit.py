"""A global cap on request bodies (issue #152, item 2).

Only the connector upload used to bound its input (64 MB, decoded); every other
JSON endpoint — ``/api/memories``, ``/api/ask``, ``/api/import`` — accepted a
body of any size, so one large POST could pin the process while it was parsed.
These tests drive the real middleware stack through ``TestClient`` and assert
the cap holds whether or not the client declares a length.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server import middleware

CAP = 4096


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("EMBEDDER_MODE", "hash")
    monkeypatch.setenv(middleware.MAX_REQUEST_BODY_BYTES_ENV, str(CAP))
    monkeypatch.delenv("LEVH_TOKEN", raising=False)
    from server import api

    # The remote-access boundary is loopback-only without a token, and
    # TestClient otherwise presents itself as a non-local client.
    with TestClient(api.app, client=("127.0.0.1", 51234)) as c:
        yield c


def _payload(size: int) -> bytes:
    return b'{"content":"' + b"x" * size + b'"}'


def test_a_body_over_the_cap_is_rejected(client):
    res = client.post(
        "/api/memories",
        content=_payload(CAP + 1),
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code == 413, res.text
    assert "limit" in res.json()["detail"]


def test_a_body_under_the_cap_still_reaches_the_route(client):
    res = client.post(
        "/api/memories",
        content=_payload(64),
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["content"].startswith("x")


def test_the_cap_cannot_be_bypassed_with_a_false_content_length(client):
    """A lying (or absent) length must not smuggle an oversized body through."""
    res = client.post(
        "/api/memories",
        content=_payload(CAP * 4),
        headers={"Content-Type": "application/json", "Content-Length": "10"},
    )
    assert res.status_code == 413, res.text


def test_an_undeclared_length_body_is_still_capped(client):
    """A chunked body has no Content-Length to check, so it is streamed."""
    res = client.post(
        "/api/memories",
        content=iter([_payload(CAP * 4)]),
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code == 413, res.text


def test_the_connector_upload_keeps_its_own_larger_allowance(client):
    """The file upload is exempt: its 64 MB decoded cap outranks the JSON one."""
    import base64

    body = base64.b64encode(b"y" * (CAP * 2)).decode()
    res = client.post(
        "/api/connectors/upload",
        json={"filename": "notes.ics", "content_b64": body},
    )
    assert res.status_code == 200, res.text
    assert res.json()["bytes"] == CAP * 2


def test_gets_are_not_affected(client):
    assert client.get("/api/health").status_code == 200


def test_the_limit_defaults_when_the_override_is_unusable(monkeypatch):
    monkeypatch.setenv(middleware.MAX_REQUEST_BODY_BYTES_ENV, "not-a-number")
    assert middleware.max_request_body_bytes() == middleware.DEFAULT_MAX_REQUEST_BODY_BYTES
    monkeypatch.setenv(middleware.MAX_REQUEST_BODY_BYTES_ENV, "0")
    assert middleware.max_request_body_bytes() == middleware.DEFAULT_MAX_REQUEST_BODY_BYTES
