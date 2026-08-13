"""Onboarding and demo-seed routes."""

from __future__ import annotations


from fastapi import APIRouter, HTTPException

from server.routes.deps import get_engine
from server.routes.models import DemoCleanupRequest, OnboardingMCPConfigRequest

router = APIRouter()


@router.post("/api/seed-demo")
async def seed_demo(force: bool = False):
    """Populate an empty store with a deterministic demo corpus (onboarding).
    Refuses to run on a non-empty store unless ``force=true``."""
    engine = await get_engine()
    return await engine.seed_demo(force=force)


@router.get("/api/onboarding/status")
async def get_onboarding_status():
    """Real first-run readiness derived from local storage/configuration."""
    engine = await get_engine()
    return await engine.onboarding_status()


@router.post("/api/onboarding/mcp-config")
async def generate_onboarding_mcp_config(req: OnboardingMCPConfigRequest):
    """Generate a focused MCP client config without persisting secrets."""
    from server.configs import PLATFORMS, generate_config, normalize_platform, render_config
    from server.tools.profiles import UnknownProfileError, profile_counts, resolve_profile

    try:
        platform = normalize_platform(req.client)
        profile = resolve_profile(req.profile)
    except (ValueError, UnknownProfileError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    from server.core.runtime_config import resolve_runtime_config, runtime_env

    runtime = resolve_runtime_config()
    cfg = generate_config(
        platform,
        project_path=".",
        profile=profile,
        **runtime_env(runtime),
    )

    # Persist only privacy-safe onboarding status so the dashboard can reflect
    # that an MCP client was configured. The generated config itself is not
    # written here because it may contain local paths or future credentials.
    from server.core.onboarding import write_receipt
    from server.core.dogfood import dogfood_enabled

    engine = await get_engine()
    status = await engine.onboarding_status()
    receipt = write_receipt(
        database_ready=True,
        first_memory_ready=status["memory_count"] > 0,
        mcp_client=req.client,
        mcp_profile=profile,
        demo_mode=bool(status["demo_seeded"]),
        dogfood_enabled=dogfood_enabled(),
    )

    return {
        "client": req.client,
        "platform": platform,
        "profile": profile,
        "tool_count": profile_counts()[profile],
        "profiles_are_security_boundary": False,
        "warning": (
            "MCP profiles reduce the advertised tool surface; they are not "
            "an authorization or security boundary."
        ),
        "onboarding_receipt_written": True,
        "onboarding_ready": receipt["first_memory_ready"],
        "config": cfg,
        # Not every client reads JSON — Codex takes TOML and Hermes YAML — so
        # the dashboard renders this text rather than JSON-encoding `config`.
        "config_text": render_config(platform, cfg),
        "config_path": PLATFORMS[platform]["file_path"],
    }


@router.post("/api/onboarding/remove-demo")
async def remove_onboarding_demo(req: DemoCleanupRequest):
    """Remove only metadata.demo=true memories using the audited purge path."""
    if not req.confirm:
        raise HTTPException(status_code=422, detail="confirmation required")
    engine = await get_engine()
    return await engine.remove_demo_data()
