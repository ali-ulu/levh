"""Lock the auth-header contract used by the DSH plugin HTTP clients."""

from pathlib import Path

import pytest

from server.auth import token_from_asgi_headers


ROOT = Path(__file__).resolve().parents[1]
DSH_CLIENTS = (
    "integrations/dsh-plugin/levh-reasoner/index.js",
    "integrations/dsh-plugin/cog-engine/index.js",
)


@pytest.mark.parametrize("relative_path", DSH_CLIENTS)
def test_dsh_plugin_uses_server_supported_token_header(relative_path: str) -> None:
    source = (ROOT / relative_path).read_text(encoding="utf-8")

    # Server auth recognizes X-LEVH-Token (case-insensitively), not the
    # generic Authorization: Bearer scheme. Keep the client contract aligned.
    assert '"X-LEVH-Token": cfg.token' in source
    assert "authorization: `Bearer ${cfg.token}`" not in source

    assert (
        token_from_asgi_headers([(b"x-levh-token", b"contract-test-token")])
        == b"contract-test-token"
    )
