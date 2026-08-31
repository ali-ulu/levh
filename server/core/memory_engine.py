"""Memory Engine — Central orchestrator for the 3-layer memory system.

Coordinates ShortTermMemory (deque), EpisodicMemory (SQLite),
VectorStore (NumPy), and H(x,ψ) scoring.

Emits events ("stored", "updated", "deleted", "recalled", "consolidated",
"session_created", "session_ended") to registered listeners so transports
(WebSocket live feed) can stream activity in real time.
"""

from __future__ import annotations

import asyncio
import os

from .database import Database
from .embedder import Embedder
from .entity_index_service import EntityIndexService
from .trust_service import TrustService
from .conflict_service import ConflictService
from .episodic import EpisodicMemory
from .hscore import HScoreCalculator, _env_float
from .short_term import ShortTermMemory
from .vector_store import VectorStore
from .engine.helpers import _COMMITMENT_PATTERN, _event_date, _event_when, _first_marker_sentence, logger  # noqa: F401
from .engine.lifecycle import MemoryLifecycleMixin
from .engine.write import MemoryWriteMixin
from .engine.attributes import MemoryAttributesMixin
from .engine.recall import MemoryRecallMixin
from .engine.explain import MemoryExplainMixin
from .engine.decay import MemoryDecayMixin
from .engine.continuity import MemoryContinuityMixin
from .engine.sessions import MemorySessionsMixin
from .engine.workspace import MemoryWorkspaceMixin
from .engine.briefing import MemoryBriefingMixin
from .engine.meeting import MemoryMeetingMixin
from .engine.transfer import MemoryTransferMixin
from .engine.ingest import MemoryIngestMixin
from .engine.privacy import MemoryPrivacyMixin
from .engine.demo import MemoryDemoMixin
from .engine.graph import MemoryGraphMixin
from .engine.dedupe import MemoryDedupeMixin
from .engine.attachments import MemoryAttachmentsMixin
from .engine.helpers import EventListener  # noqa: F401
from .agent_tracker import AgentTracker












class MemoryEngine(
    MemoryLifecycleMixin,
    MemoryWriteMixin,
    MemoryAttributesMixin,
    MemoryRecallMixin,
    MemoryExplainMixin,
    MemoryDecayMixin,
    MemoryContinuityMixin,
    MemorySessionsMixin,
    MemoryWorkspaceMixin,
    MemoryBriefingMixin,
    MemoryMeetingMixin,
    MemoryTransferMixin,
    MemoryIngestMixin,
    MemoryPrivacyMixin,
    MemoryDemoMixin,
    MemoryGraphMixin,
    MemoryDedupeMixin,
    MemoryAttachmentsMixin,
):
    """Singleton-like engine that coordinates all memory layers."""

    def __init__(
        self,
        db_path: str | None = None,
        embedder_mode: str | None = None,
        short_term_max: int | None = None,
    ):
        if db_path is None or embedder_mode is None or short_term_max is None:
            from .runtime_config import resolve_runtime_config

            runtime = resolve_runtime_config()
            db_path = db_path or runtime.database_path
            embedder_mode = embedder_mode or runtime.embedder_mode
            short_term_max = short_term_max if short_term_max is not None else runtime.short_term_max

        self.db = Database(db_path)
        self.short_term = ShortTermMemory(max_size=short_term_max)
        self.episodic = EpisodicMemory(self.db)
        self.vector_store = VectorStore()
        self.scorer = HScoreCalculator()
        self._initialized = False
        self._init_lock = asyncio.Lock()
        self._listeners: list[EventListener] = []
        self._derived_dirty = False
        self._refreshing_derived = False
        self.entity_index = EntityIndexService(self.db, self.episodic, self._emit)
        self.trust_service = TrustService(
            self.db,
            self.episodic,
            self.entity_index,
            self._emit,
        )
        self.conflict_service = ConflictService(
            self.db,
            self.episodic,
            self.entity_index,
            self._emit,
            self.memory_feedback,
            self._mark_derived_dirty,
        )

        # Agent tracker: tracks connected agents, presence, checkpoints
        self.agent_tracker = AgentTracker(self.db, self._emit)

        # Retroactive interference: a new memory that is near-identical to an
        # older one weakens the older one (it is being superseded). The default
        # threshold (0.97) only fires on true near-duplicates in every embedder
        # mode; with a semantic embedder, 0.88-0.92 also catches contradictions.
        self.interference_threshold = _env_float("INTERFERENCE_THRESHOLD", 0.97)
        self.interference_factor = _env_float("INTERFERENCE_FACTOR", 0.6)

        # Auto-capture: on end_session, distill the session's memories into one
        # durable summary memory (LLM if available, extractive fallback else).
        self.auto_summarize = os.getenv(
            "AUTO_SUMMARIZE_SESSIONS", ""
        ).strip().lower() in ("1", "true", "yes", "on")

        # Lazy-init embedder (downloads model on first use)
        self._embedder: Embedder | None = None
        self._embedder_mode = embedder_mode

        # Cross-process/cross-instance cache coherence (see
        # _sync_with_external_writes). None until initialize() sets a
        # baseline; a synced engine's own writes never change its own view of
        # data_version, so this only ever detects a peer's writes. The lock
        # serializes concurrent refreshes on this engine (e.g. several
        # recall() calls arriving together right after a peer's write) so
        # they don't all pay for a redundant full reload.
        self._known_data_version: int | None = None
        self._sync_lock = asyncio.Lock()

    @property
    def embedder(self) -> Embedder:
        if self._embedder is None:
            self._embedder = Embedder(mode=self._embedder_mode)
            self.vector_store.dimension = self._embedder.dimension
        return self._embedder

    # ── Events ─────────────────────────────────────────────────────







    # ── Lifecycle ─────────────────────────────────────────────────




    # ── Memory Operations ─────────────────────────────────────────










    # ── Consolidation ─────────────────────────────────────────────



    # ── Importance / Pinning ──────────────────────────────────────



    # ── Reinforcement (spaced-repetition-style memory strengthening) ──




    # ── Spaced-repetition review (Faz 5: close the lifecycle loop) ─




    # ── Related memories (graph-lite) ─────────────────────────────


    # ── Context Window ────────────────────────────────────────────


    # ── Context File Generation (CLAUDE.md / .cursorrules) ───────


    # ── Sessions ───────────────────────────────────────────────────







    # ── Continuity Context (autonomous session recovery) ────────────


    # ── Projects / Sources / Tags ─────────────────────────────────




    # ── People (entity graph over captured metadata) ──────────────



    # ── Organizations (people graph grouped by email domain) ───────







    # ── Statistics ─────────────────────────────────────────────────


    # ── Score Breakdown (for visualization) ───────────────────────


    # ── Export / Import ────────────────────────────────────────────




    # ── Backup / Restore (Faz 0 security) ─────────────────────────



    # ── Admission Gate (quality: decide before storing) ───────────



    # ── Connector ingest v2 (gate-integrated, incremental) ────────



    # ── Hard-delete audit & redaction (trust) ─────────────────────






    # ── Onboarding: demo seed (2.23B) ─────────────────────────────




    # ── Entity knowledge graph (Faz 2) ────────────────────────────





    # ── Provenance / trust score (deterministic, NOT truth) ───────







    # ── Conflict candidates (deterministic review signal) ─────────




    # ── Deduplication ─────────────────────────────────────────────



    # ── Consolidation (Faz 5: sleep-like memory compression) ──────

