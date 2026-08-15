"""The dashboard must not let browsers cache the HTML that names bundle hashes.

Regression guard: the static mount used to ship no Cache-Control at all, so
browsers fell back to heuristic freshness and reused a stale index.html for
hours. After an upgrade that document points at `_next/static` bundles that no
longer exist, hydration dies, and the user sees only "a client-side exception
has occurred".
"""

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["EMBEDDER_MODE"] = "hash"

from server import api  # noqa: E402


@pytest.fixture
def client():
    if api._FRONTEND_DIR is None:
        pytest.skip("dashboard static export not built in this checkout")
    # The remote-access boundary only trusts loopback clients.
    return TestClient(api.app, client=("127.0.0.1", 8000))


def test_the_dashboard_document_is_always_revalidated(client):
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"


def test_nested_dashboard_routes_are_always_revalidated(client):
    response = client.get("/memories/")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"


def test_content_hashed_bundles_are_cached_forever(client):
    static_root = os.path.join(api._FRONTEND_DIR, "_next", "static")
    if not os.path.isdir(static_root):
        pytest.skip("no _next/static directory in the built export")

    bundle = next(
        (
            os.path.relpath(os.path.join(root, name), api._FRONTEND_DIR)
            for root, _dirs, names in os.walk(static_root)
            for name in names
            if name.endswith(".js")
        ),
        None,
    )
    assert bundle is not None, "expected at least one built JS bundle"

    response = client.get("/" + bundle.replace(os.sep, "/"))

    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
