"""Environment variable compatibility helpers for the LEVH rename."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger("levh.env")
_warned_legacy: set[str] = set()


def get_env(
    name: str,
    default: Any = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> Any:
    """Read a LEVH environment variable with a legacy StackMemory fallback.

    New ``LEVH_*`` variables always win. Legacy ``STACKMEMORY_*`` variables
    remain supported for existing installations and emit one warning per
    process when used.
    """
    env = os.environ if environ is None else environ
    if name.startswith("LEVH_"):
        canonical = name
    else:
        canonical = "LEVH_" + name
        if canonical in env and str(env[canonical]).strip() != "":
            return env[canonical]
        if name in env and str(env[name]).strip() != "":
            return env[name]

    if canonical in env and str(env[canonical]).strip() != "":
        return env[canonical]

    legacy = name
    if name.startswith("LEVH_"):
        legacy = "STACKMEMORY_" + name.removeprefix("LEVH_")
    elif not name.startswith("STACKMEMORY_"):
        legacy = "STACKMEMORY_" + name
    if legacy in env and str(env[legacy]).strip() != "":
        if legacy not in _warned_legacy:
            logger.warning(
                "Environment variable %s is deprecated; use %s",
                legacy,
                name,
            )
            _warned_legacy.add(legacy)
        return env[legacy]
    return default
