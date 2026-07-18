"""Desired invariant for content mutation admission and secret handling.

The engine/REST/MCP persistence characterization is retained under
evidence/groundtruth/task-00A3/harness/. The canary below is synthetic and
cannot authenticate to any service.
"""

from pathlib import Path

import pytest

from server.core.memory_engine import MemoryEngine


P0_3_REASON = (
    "P0-3 confirmed: content update bypasses admission and persists the raw "
    "secret canary"
)


@pytest.mark.xfail(strict=True, reason=P0_3_REASON)
@pytest.mark.asyncio
async def test_content_update_never_embeds_or_persists_raw_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = MemoryEngine(
        db_path=str(tmp_path / "update-admission.db"),
        embedder_mode="hash",
    )
    await engine.initialize()
    try:
        created = await engine.admit_memory(
            "GT00A5 safe mutation baseline",
            project="GT00A5_P0_3_INVARIANT",
            memory_type="episodic",
        )
        assert created["stored"] is True
        memory_id = created["memory"]["id"]
        canary = "GT00A5_SYNTHETIC_SECRET_NEVER_VALID"
        candidate = f"api_key={canary} synthetic update canary"
        embedded_inputs: list[str] = []
        original_embed = engine.embedder.embed

        async def capture_embed(text: str) -> list[float]:
            embedded_inputs.append(text)
            return await original_embed(text)

        monkeypatch.setattr(engine.embedder, "embed", capture_embed)
        await engine.update_memory(memory_id, content=candidate)
        persisted = await engine.episodic.get(memory_id)
        assert persisted is not None
        assert canary not in persisted.content
        assert all(canary not in text for text in embedded_inputs)
    finally:
        await engine.shutdown()
