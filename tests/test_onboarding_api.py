from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from server.core.memory_engine import MemoryEngine


@pytest.mark.asyncio
async def test_onboarding_api_status_and_config(tmp_path, monkeypatch):
    import server.api as api_mod

    monkeypatch.chdir(tmp_path)
    if api_mod._engine is not None:
        await api_mod._engine.shutdown()
    api_mod._engine = MemoryEngine(db_path=str(tmp_path / "api.db"), embedder_mode="hash")
    await api_mod._engine.initialize()
    api_mod._initialized = True
    try:
        transport = ASGITransport(app=api_mod.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            status = await client.get("/api/onboarding/status")
            assert status.status_code == 200
            assert status.json()["first_run"] is True

            config = await client.post(
                "/api/onboarding/mcp-config",
                json={"client": "claude", "profile": "work"},
            )
            assert config.status_code == 200
            body = config.json()
            assert body["profile"] == "work"
            assert body["tool_count"] == 32
            assert body["profiles_are_security_boundary"] is False
            assert body["onboarding_receipt_written"] is True
            assert body["onboarding_ready"] is False

            configured = await client.get("/api/onboarding/status")
            assert configured.status_code == 200
            assert configured.json()["mcp_configured"] is True
            assert configured.json()["mcp_client"] == "claude"
            assert configured.json()["mcp_profile"] == "work"

            # The demo-data surface is gone: neither endpoint is in the contract
            # any more. (A POST to the path now falls through to the dashboard
            # static mount, so the schema is the honest place to assert this.)
            schema = api_mod.app.openapi()["paths"]
            assert "/api/v1/seed-demo" not in schema
            assert "/api/v1/onboarding/remove-demo" not in schema

            read_only = await client.post(
                "/api/onboarding/mcp-config",
                json={"client": "claude", "profile": "read_only"},
            )
            assert read_only.status_code == 200
            ro_body = read_only.json()
            assert ro_body["profile"] == "read_only"
            assert ro_body["tool_count"] == 39
            assert ro_body["profiles_are_security_boundary"] is True
            assert "server-enforced" in ro_body["warning"]
    finally:
        await api_mod._engine.shutdown()
        api_mod._engine = None
        api_mod._initialized = False
