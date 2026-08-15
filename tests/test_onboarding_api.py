from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from server.core.memory_engine import MemoryEngine


@pytest.mark.asyncio
async def test_onboarding_api_status_config_and_demo_cleanup(tmp_path, monkeypatch):
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
            assert body["tool_count"] == 17
            assert body["profiles_are_security_boundary"] is False
            assert body["onboarding_receipt_written"] is True
            assert body["onboarding_ready"] is False

            configured = await client.get("/api/onboarding/status")
            assert configured.status_code == 200
            assert configured.json()["mcp_configured"] is True
            assert configured.json()["mcp_client"] == "claude"
            assert configured.json()["mcp_profile"] == "work"

            seeded = await client.post("/api/seed-demo")
            assert seeded.status_code == 200 and seeded.json()["seeded"] == 20

            ready = await client.get("/api/onboarding/status")
            assert ready.status_code == 200
            assert ready.json()["ready"] is True

            denied = await client.post("/api/onboarding/remove-demo", json={"confirm": False})
            assert denied.status_code == 422

            cleaned = await client.post("/api/onboarding/remove-demo", json={"confirm": True})
            assert cleaned.status_code == 200
            assert cleaned.json()["removed"] == 20
            assert cleaned.json()["remaining"] == 0
    finally:
        await api_mod._engine.shutdown()
        api_mod._engine = None
        api_mod._initialized = False
