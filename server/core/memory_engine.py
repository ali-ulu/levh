"""Memory Engine — Central orchestrator for the 3-layer memory system.

Coordinates ShortTermMemory (deque), EpisodicMemory (SQLite),
VectorStore (NumPy), and H(x,ψ) scoring.

Emits events ("stored", "updated", "deleted", "recalled", "consolidated",
"session_created", "session_ended") to registered listeners so transports
(WebSocket live feed) can stream activity in real time.
"""

from __future__ import annotations

from .database import Database
from .embedder import Embedder
from .engine_factory import EngineConfig, wire_engine
from .episodic import EpisodicMemory
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
from .engine.reembed import MemoryReembedMixin
from .engine.helpers import EventListener  # noqa: F401












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
    MemoryReembedMixin,
):
    """Coordinates the memory layers and services.

    Orchestration-only (issue #95): this class owns no construction policy.
    ``__init__`` resolves an :class:`EngineConfig` and hands wiring to
    :func:`server.core.engine_factory.wire_engine` — the single place that
    decides which collaborators exist and how they reference each other.
    The engine's own code only coordinates them (lifecycle, events,
    derived-state scheduling).
    """

    # Declared here, assigned in ``wire_engine`` (see engine_factory). Naming
    # the two storage collaborators at the class level is what lets a module
    # typed against ``MemoryEngine`` — procedure.py, guard.py — resolve
    # ``engine.db`` and ``engine.episodic`` without mypy's
    # ``follow_imports = "silent"`` hiding the mixins that hold them (#350).
    db: Database
    episodic: EpisodicMemory

    def __init__(
        self,
        db_path: str | None = None,
        embedder_mode: str | None = None,
        short_term_max: int | None = None,
    ):
        self.config = EngineConfig(db_path, embedder_mode, short_term_max)
        wire_engine(self)

    @property
    def embedder(self) -> Embedder:
        if self._embedder is None:
            self._embedder = Embedder(mode=self._embedder_mode)
            self.vector_store.dimension = self._embedder.dimension
            if self._embedder.fallback_reason:
                # `auto`/`local` silently degrading to hash embeddings is not
                # a crash, so nothing else would ever tell an operator it
                # happened -- and hash's non-semantic scoring can misfire on
                # the admission gate's duplicate check for genuinely distinct
                # content (#78). Loud at the point of decision, not buried in
                # a debug flag.
                logger.warning(
                    "Embedder requested=%s resolved=hash: %s",
                    self._embedder.requested_mode,
                    self._embedder.fallback_reason,
                )
        return self._embedder
