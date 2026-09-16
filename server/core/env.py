"""Environment variable compatibility helpers for the LEVH rename.

This module is the single source of truth for *which* environment variable
names the application reads: ``get_env`` is the only reader, and
``accepted_env_var_names`` describes its acceptance set so consumers that must
neutralize the environment (the test suite) can derive their scrub list from
here instead of hand-copying a snapshot of it.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger("levh.env")
_warned_legacy: set[str] = set()


def accepted_env_var_names(name: str) -> tuple[str, ...]:
    """Every spelling ``get_env(name, ...)`` reads, in precedence order.

    ``get_env`` is lenient by design for the LEVH rename: a canonical
    ``LEVH_*`` name, the bare name and the legacy ``STACKMEMORY_*`` name all
    resolve to the same setting. A caller that wants *none* of them read must
    scrub all of them — a list that previously lived only inside ``get_env``.

    Derived mechanically from the same rules ``get_env`` implements, so the
    two cannot drift: add a new accepted spelling to ``get_env`` and the set
    grows here too.
    """
    if name.startswith("STACKMEMORY_"):
        # Already legacy: recursing it would never terminate. It reads the
        # canonical LEVH_-prefixed spelling of itself, then itself.
        return ("LEVH_" + name, name)
    if name.startswith("LEVH_"):
        legacy = "STACKMEMORY_" + name.removeprefix("LEVH_")
        return (name, legacy)
    return ("LEVH_" + name, name, "STACKMEMORY_" + name)


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
    candidates = accepted_env_var_names(name)
    for candidate in candidates:
        if candidate in env and str(env[candidate]).strip() != "":
            if candidate.startswith("STACKMEMORY_"):
                _warn_legacy(candidate, candidates[0])
            return env[candidate]
    return default


def _warn_legacy(candidate: str, name: str) -> None:
    if candidate not in _warned_legacy:
        logger.warning("Environment variable %s is deprecated; use %s", candidate, name)
        _warned_legacy.add(candidate)
