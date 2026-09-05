"""The librarian widget is injected into dashboard HTML — without corrupting it.

Rewriting a response body invalidates the headers that described the old one.
Carrying them over produced a response the browser cannot resolve, and the
symptom is not an error: it is a page that silently fails to load. Each test
here is one header that must not survive the rewrite, plus the case where the
body must not be touched at all.
"""

from __future__ import annotations

import os
import tempfile

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

os.environ["EMBEDDER_MODE"] = "hash"

from server.core.memory_engine import MemoryEngine  # noqa: E402


@pytest_asyncio.fixture
async def client_and_app():
    import server.api as api_mod

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    if api_mod._engine is not None:
        await api_mod._engine.shutdown()
    api_mod._engine = MemoryEngine(db_path=path, embedder_mode="hash", short_term_max=20)
    await api_mod.get_engine()
    api_mod._initialized = True
    transport = ASGITransport(app=api_mod.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, api_mod.app
    await api_mod._engine.shutdown()
    api_mod._engine = None
    api_mod._initialized = False
    if os.path.exists(path):
        os.unlink(path)


@pytest.mark.asyncio
async def test_the_widget_script_is_injected_into_html(client_and_app):
    client, _app = client_and_app
    body = (await client.get("/librarian")).text
    assert '<script src="/librarian.js"></script></body>' in body


@pytest.mark.asyncio
async def test_the_widget_is_served_as_javascript_not_html(client_and_app):
    """text/html made the browser refuse the script under nosniff, and the
    symptom was an invisible widget with no error to trace."""
    client, _app = client_and_app
    resp = await client.get("/librarian.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]


@pytest.mark.asyncio
async def test_content_length_matches_the_rewritten_body(client_and_app):
    """The stale length truncated or hung the response."""
    client, _app = client_and_app
    resp = await client.get("/librarian")
    declared = resp.headers.get("content-length")
    if declared is not None:
        assert int(declared) == len(resp.content)


@pytest.fixture
def probe_route(client_and_app):
    """Register a route ahead of the dashboard's catch-all static mount, and
    take it out again afterwards.

    Ahead, because appending puts it *after* the mount at "/", which answers
    everything with the dashboard's 404 page — HTML, so it gets injected too,
    and a test asserting on injected HTML would pass without ever reaching its
    own route. Out again, because the app object is shared across the whole
    session: a probe left behind shows up in the OpenAPI schema and fails
    tests/test_docs_match_code.py as an undocumented route.
    """
    _client, app = client_and_app
    added: list = []

    def _add(path: str, handler) -> None:
        app.add_api_route(path, handler, methods=["GET"])
        route = app.router.routes.pop()
        app.router.routes.insert(0, route)
        added.append(route)

    yield _add

    for route in added:
        app.router.routes.remove(route)
    app.openapi_schema = None  # rebuilt on next request; drops the probe


@pytest.mark.asyncio
async def test_a_compressed_response_is_passed_through_untouched(client_and_app, probe_route):
    """A gzip body cannot be string-replaced. Rewriting it while keeping
    content-encoding: gzip produced plain text labelled as compressed."""
    import gzip

    from starlette.responses import Response

    client, app = client_and_app
    original = b"<html><body>compressed</body></html>"

    async def _gzip_html():
        return Response(
            gzip.compress(original),
            media_type="text/html",
            headers={"content-encoding": "gzip"},
        )

    probe_route("/__test_gzip_html", _gzip_html)

    resp = await client.get("/__test_gzip_html")
    assert resp.headers.get("content-encoding") == "gzip"
    # Still decodable, and still exactly what the route produced: the body was
    # passed through rather than rewritten behind a now-false encoding header.
    assert resp.content == original
    assert "/librarian.js" not in resp.text


@pytest.mark.asyncio
async def test_a_stale_etag_does_not_survive_a_rewrite(client_and_app, probe_route):
    """An etag describes a body. After injection it describes a body that was
    never sent, so a conditional request would be answered with the wrong one."""
    from starlette.responses import HTMLResponse

    client, app = client_and_app

    async def _etag_html():
        return HTMLResponse("<html><body>hi</body></html>",
                            headers={"etag": '"original"'})

    probe_route("/__test_etag_html", _etag_html)

    resp = await client.get("/__test_etag_html")
    assert resp.status_code == 200               # the probe route, not the 404 page
    assert "hi" in resp.text                     # ... and its body
    assert "/librarian.js" in resp.text          # it was rewritten
    assert resp.headers.get("etag") is None      # so the old validator is gone


@pytest.mark.asyncio
async def test_json_responses_are_not_touched(client_and_app):
    client, _app = client_and_app
    resp = await client.get("/api/health")
    assert "librarian.js" not in resp.text
