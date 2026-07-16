"""Shared MemoryEngine provider.

All transports (REST API, MCP stdio, MCP SSE) must operate on the SAME
engine instance so the short-term deque, vector store, and event stream
stay consistent no matter which channel a client uses.
"""

from __future__ import annotations

from .memory_engine import MemoryEngine
from .runtime_config import resolve_runtime_config

_engine: MemoryEngine | None = None


def get_engine() -> MemoryEngine:
    """Return the process-wide engine, creating it from env config if needed.

    Does NOT initialize (connect the DB) — callers await engine.initialize(),
    which is idempotent.
    """
    global _engine
    if _engine is None:
        config = resolve_runtime_config()
        _engine = MemoryEngine(
            db_path=config.database_path,
            embedder_mode=config.embedder_mode,
            short_term_max=config.short_term_max,
        )
        # Opt-in local dogfood instrumentation (STACKMEMORY_DOGFOOD_ENABLED).
        # No-op by default; journals to a local file only, never the network.
        from .dogfood import maybe_attach

        maybe_attach(_engine)
    return _engine


def set_engine(engine: MemoryEngine | None) -> None:
    """Replace the shared engine (used by tests)."""
    global _engine
    _engine = engine
