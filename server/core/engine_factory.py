"""MemoryEngine construction — the one place services are wired.

Split out of ``memory_engine.py`` (issue #95): the engine class stays
orchestration-only; this factory owns *which* collaborators exist and how
they reference each other. The dependency direction is fixed here:

    Database  <-  EpisodicMemory  <-  services (entity, trust, conflict)
    AgentTracker(db), ShortTermMemory, VectorStore, HScoreCalculator

The engine receives them via ``MemoryEngine.build(...)`` and never
constructs a collaborator itself.
"""

from __future__ import annotations

import asyncio
import os

from .database import Database
from .entity_index_service import EntityIndexService
from .conflict_service import ConflictService
from .episodic import EpisodicMemory
from .hscore import HScoreCalculator, _env_float
from .short_term import ShortTermMemory
from .trust_service import TrustService
from .vector_store import VectorStore
from .agent_tracker import AgentTracker


class EngineConfig:
    """Resolved constructor settings for a MemoryEngine.

    Mirrors the historical ``MemoryEngine(db_path, embedder_mode,
    short_term_max)`` contract: ``None`` fields fall back to the canonical
    runtime configuration.
    """

    def __init__(
        self,
        db_path: str | None = None,
        embedder_mode: str | None = None,
        short_term_max: int | None = None,
    ) -> None:
        if db_path is None or embedder_mode is None or short_term_max is None:
            from .runtime_config import resolve_runtime_config

            runtime = resolve_runtime_config()
            db_path = db_path or runtime.database_path
            embedder_mode = embedder_mode or runtime.embedder_mode
            short_term_max = short_term_max if short_term_max is not None else runtime.short_term_max
        self.db_path = db_path
        self.embedder_mode = embedder_mode
        self.short_term_max = short_term_max


def wire_engine(engine) -> None:
    """Construct and attach every collaborator the engine coordinates.

    ``engine`` is an unconstructed MemoryEngine instance; this function
    populates its attributes exactly as ``MemoryEngine.__init__`` did before
    the wiring moved here (issue #95). The engine keeps one owner per piece
    of state: the factory creates them once, the engine only coordinates.
    """
    config: EngineConfig = engine.config

    engine.db = Database(config.db_path)
    engine.short_term = ShortTermMemory(max_size=config.short_term_max)
    engine.episodic = EpisodicMemory(engine.db)
    engine.vector_store = VectorStore()
    engine.scorer = HScoreCalculator()
    engine._initialized = False
    engine._init_lock = asyncio.Lock()
    engine._listeners = []
    engine._derived_dirty = False
    engine._refreshing_derived = False
    engine._derived_task = None
    engine._derived_retry_count = 0
    engine._derived_retry_wake = asyncio.Event()
    engine.entity_index = EntityIndexService(engine.db, engine.episodic, engine._emit)
    engine.trust_service = TrustService(
        engine.db,
        engine.episodic,
        engine.entity_index,
        engine._emit,
    )
    engine.conflict_service = ConflictService(
        engine.db,
        engine.episodic,
        engine.entity_index,
        engine._emit,
        engine.memory_feedback,
        engine._mark_derived_dirty,
    )

    # Agent tracker: tracks connected agents, presence, checkpoints
    engine.agent_tracker = AgentTracker(engine.db, engine._emit)

    # Retroactive interference: a new memory that is near-identical to an
    # older one weakens the older one (it is being superseded). The default
    # threshold (0.97) only fires on true near-duplicates in every embedder
    # mode; with a semantic embedder, 0.88-0.92 also catches contradictions.
    engine.interference_threshold = _env_float("INTERFERENCE_THRESHOLD", 0.97)
    engine.interference_factor = _env_float("INTERFERENCE_FACTOR", 0.6)

    # Auto-capture: on end_session, distill the session's memories into one
    # durable summary memory (LLM if available, extractive fallback else).
    engine.auto_summarize = os.getenv(
        "AUTO_SUMMARIZE_SESSIONS", ""
    ).strip().lower() in ("1", "true", "yes", "on")

    # Lazy-init embedder (downloads model on first use)
    engine._embedder = None
    engine._embedder_mode = config.embedder_mode

    # Cross-process/cross-instance cache coherence (see
    # _sync_with_external_writes). None until initialize() sets a baseline.
    engine._known_data_version = None
    engine._sync_lock = asyncio.Lock()
