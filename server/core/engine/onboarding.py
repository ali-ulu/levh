"""First-run onboarding readiness.

Part of :class:`server.core.memory_engine.MemoryEngine`, split out to keep
each file readable. The computation itself lives in
:mod:`server.core.onboarding`; this mixin is only the engine-bound entry point.
"""

from __future__ import annotations


class MemoryOnboardingMixin:
    """First-run readiness computed from real engine state."""

    async def onboarding_status(self) -> dict:
        """Return deterministic first-run readiness computed from real state."""
        from ..onboarding import onboarding_status

        return await onboarding_status(self)
