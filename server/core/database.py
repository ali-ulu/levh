"""SQLite Database Layer — Zero-ops persistence for LEVH.

Composition, not inheritance (issue #94): ``Database`` is a thin facade that
owns the connection lifecycle, schema/migrations and transactions, and holds
one instance of each query group (``server.core.db.*``) injected through
their constructors. Data flows one way: callers ask the facade (or the
engine asks it), the facade owns the connection, the query groups use it.

Backwards compatibility: every historical ``db.<method>()`` call site still
works. ``__getattr__`` resolves any name not defined here against the
composed query groups, so the ~79 former mixin methods keep their old
addresses without this file re-listing them.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Optional

import aiosqlite

from server.core.env import get_env
from . import metrics
from .db.schema import CURRENT_SCHEMA_VERSION, _FTS_SCHEMA, _INDEXES, _MIGRATIONS, _SCHEMA, default_db_path
from .db.aggregates import AggregateQueries
from .db.attachments import AttachmentQueries
from .db.entities import EntityQueries
from .db.continuity_log import ContinuityLogQueries
from .db.findings import FindingQueries
from .db.guard import GuardQueries
from .db.held import HeldMemoryQueries
from .db.memories import MemoryQueries
from .db.recall_log import RecallLogQueries
from .db.sessions import SessionQueries
from .db.snapshot import SnapshotQueries
from .db.trust import TrustQueries

DEFAULT_BUSY_TIMEOUT_MS = 5_000


class Database:
    """Async SQLite wrapper with auto-init.

    Owns: the connection, PRAGMAs, schema + migrations, commit/close and the
    cross-process change counter. Composes: one query group per concern,
    each constructed with this facade so its SQL reaches ``self._db.conn``.
    """

    def __init__(self, db_path: str | None = None):
        # Resolved here, not in the signature (issue #143): a default baked
        # into the signature froze ``get_env``'s answer at import time, so a
        # reload or a later-set env var was ignored and a relative default
        # followed the working directory. ``default_db_path()`` reads the
        # environment now and returns an absolute path.
        self.db_path = db_path if db_path is not None else default_db_path()
        self._connection: Optional[aiosqlite.Connection] = None
        try:
            configured_timeout = int(
                get_env("LEVH_SQLITE_BUSY_TIMEOUT_MS", str(DEFAULT_BUSY_TIMEOUT_MS))
            )
        except ValueError:
            configured_timeout = DEFAULT_BUSY_TIMEOUT_MS
        self.busy_timeout_ms = max(0, configured_timeout)
        self.fts5_available = False
        self.schema_version = 0

        # The query groups, composed (issue #94). Each gets the facade so it
        # reads the live connection through ``self._db.conn``.
        self.memories = MemoryQueries(self)
        self.aggregates = AggregateQueries(self)
        self.sessions = SessionQueries(self)
        self.entities = EntityQueries(self)
        self.trust = TrustQueries(self)
        self.guard = GuardQueries(self)
        self.snapshot = SnapshotQueries(self)
        self.attachments = AttachmentQueries(self)
        self.held = HeldMemoryQueries(self)
        self.findings = FindingQueries(self)
        self.recall_log = RecallLogQueries(self)
        self.continuity_log = ContinuityLogQueries(self)
        self._groups = (
            self.memories,
            self.aggregates,
            self.sessions,
            self.entities,
            self.trust,
            self.guard,
            self.snapshot,
            self.attachments,
            self.held,
            self.findings,
            self.recall_log,
            self.continuity_log,
        )

    async def connect(self) -> None:
        """Open connection and create tables if needed. Safe to call twice."""
        if self._connection is not None:
            return
        parent = Path(self.db_path).parent
        if str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)
        self._connection = await aiosqlite.connect(
            self.db_path,
            timeout=max(self.busy_timeout_ms / 1000.0, 0.001),
        )
        self._connection.row_factory = aiosqlite.Row
        await self._connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        await self._connection.execute("PRAGMA foreign_keys = ON")
        if self.db_path != ":memory:":
            # WAL allows readers and a writer to coexist across REST/MCP/CLI
            # processes. NORMAL is SQLite's recommended durability/performance
            # pairing for WAL while retaining crash safety.
            await self._connection.execute("PRAGMA journal_mode = WAL")
            await self._connection.execute("PRAGMA synchronous = NORMAL")
        await self._connection.executescript(_SCHEMA)
        await self._migrate()
        await self._connection.executescript(_INDEXES)
        await self._connection.commit()

    async def _migrate_legacy_columns(self) -> None:
        """Bring pre-versioned databases up to the v1 column contract."""
        cursor = await self._connection.execute("PRAGMA table_info(memories)")
        existing = {row[1] for row in await cursor.fetchall()}
        await cursor.close()
        for column, ddl in _MIGRATIONS:
            if column not in existing:
                await self._connection.execute(ddl)

    async def _backfill_validity(self) -> None:
        """Give pre-existing rows a ``valid_from`` (issue #335).

        ``valid_from`` is backfilled from ``created_at`` — the only world-time
        signal an old row carries, and the honest reading of "this fact has
        been believed since we recorded it". ``valid_to``/``superseded_by``
        are deliberately **not** backfilled from the legacy
        ``metadata.superseded_by`` pointer: that pointer is the wide
        *interference* signal (any same-project near neighbour), not a
        retirement, and treating it as one would retire rows a user never
        superseded. Retirement is a new, opt-in concept, so old rows start
        current (``valid_to`` NULL). Idempotent: guarded on
        ``valid_from IS NULL``, so a second connect is a no-op.
        """
        await self._connection.execute(
            "UPDATE memories SET valid_from = created_at WHERE valid_from IS NULL"
        )

    async def _backfill_workspace(self) -> None:
        """Put every pre-existing row in the one implicit workspace (#302).

        A store written before tenancy existed is a single-user store; the
        honest reading is that all of it belongs to ``default``. Idempotent:
        guarded on ``workspace_id IS NULL``, so a second connect is a no-op and
        a row deliberately written to another workspace is never moved.
        """
        await self._connection.execute(
            "UPDATE memories SET workspace_id = 'default' WHERE workspace_id IS NULL"
        )

    async def _migrate_recall_audit(self) -> None:
        """Add the Phase 2 principal/workspace audit columns to recall_log."""
        cursor = await self._connection.execute("PRAGMA table_info(recall_log)")
        existing = {row[1] for row in await cursor.fetchall()}
        await cursor.close()
        additions = (
            ("workspace_id", "ALTER TABLE recall_log ADD COLUMN workspace_id TEXT NOT NULL DEFAULT 'default'"),
            ("principal_id", "ALTER TABLE recall_log ADD COLUMN principal_id TEXT NOT NULL DEFAULT 'local'"),
            ("principal_role", "ALTER TABLE recall_log ADD COLUMN principal_role TEXT NOT NULL DEFAULT 'admin'"),
        )
        for column, ddl in additions:
            if column not in existing:
                await self._connection.execute(ddl)

    async def _migrate_team_handoff_scheduler(self) -> None:
        """Add Phase 5 scheduler columns to existing v8 handoff tables."""
        cursor = await self._connection.execute("PRAGMA table_info(team_handoffs)")
        existing = {row[1] for row in await cursor.fetchall()}
        await cursor.close()
        additions = (
            (
                "required_capabilities_json",
                "ALTER TABLE team_handoffs ADD COLUMN "
                "required_capabilities_json TEXT NOT NULL DEFAULT '[]'",
            ),
            (
                "priority",
                "ALTER TABLE team_handoffs ADD COLUMN "
                "priority INTEGER NOT NULL DEFAULT 0",
            ),
            (
                "accepted_session_id",
                "ALTER TABLE team_handoffs ADD COLUMN accepted_session_id TEXT",
            ),
        )
        for column, ddl in additions:
            if column not in existing:
                await self._connection.execute(ddl)

    async def _migrate_held_workspace(self) -> None:
        """Put pre-Phase-2 held candidates in the implicit default workspace."""
        cursor = await self._connection.execute("PRAGMA table_info(held_memories)")
        existing = {row[1] for row in await cursor.fetchall()}
        await cursor.close()
        if "workspace_id" not in existing:
            await self._connection.execute(
                "ALTER TABLE held_memories ADD COLUMN workspace_id "
                "TEXT NOT NULL DEFAULT 'default'"
            )

    async def _set_user_version(self, version: int) -> None:
        await self._connection.execute(f"PRAGMA user_version = {int(version)}")
        self.schema_version = int(version)

    async def _has_fts5_table(self) -> bool:
        cursor = await self._connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memories_fts'"
        )
        row = await cursor.fetchone()
        await cursor.close()
        return bool(row)

    async def _install_fts5(self) -> bool:
        """Install and backfill the optional FTS5 index.

        Python's standard SQLite builds normally include FTS5. When a vendor
        build omits it, LEVH remains functional and falls back to LIKE;
        the schema version intentionally remains at v1 so a later compatible
        runtime can retry the migration.
        """
        try:
            await self._connection.executescript(_FTS_SCHEMA)
            await self._connection.execute("DELETE FROM memories_fts")
            await self._connection.execute(
                "INSERT INTO memories_fts(memory_id, content) SELECT id, content FROM memories"
            )
        except aiosqlite.OperationalError as exc:
            if "fts5" not in str(exc).lower():
                raise
            self.fts5_available = False
            return False
        self.fts5_available = True
        return True

    async def _migrate(self) -> None:
        """Run numbered, monotonic migrations using ``PRAGMA user_version``."""
        cursor = await self._connection.execute("PRAGMA user_version")
        row = await cursor.fetchone()
        await cursor.close()
        version = int(row[0] if row else 0)
        if version > CURRENT_SCHEMA_VERSION:
            raise RuntimeError(
                f"database schema v{version} is newer than supported v{CURRENT_SCHEMA_VERSION}"
            )

        if version < 1:
            await self._migrate_legacy_columns()
            version = 1
            await self._set_user_version(version)

        if version < 2:
            if await self._install_fts5():
                version = 2
                await self._set_user_version(version)
        else:
            self.fts5_available = await self._has_fts5_table()
            if not self.fts5_available:
                self.fts5_available = await self._install_fts5()

        if version < 3:
            # Additive column pass so a v2 store gains the bi-temporal columns
            # (#335) even though it already has the v1 set, then backfill the
            # rows already on disk.
            await self._migrate_legacy_columns()
            await self._backfill_validity()
            version = 3
            await self._set_user_version(version)

        if version < 4:
            # Tenancy boundary (#302). A v3 store predates the column, so this
            # adds it and stamps every existing row into the implicit
            # ``default`` workspace — the single-user install's one workspace.
            # ``_backfill_validity`` runs too: it is idempotent and repairs a v3
            # store whose rows were written directly with NULL validity (the
            # schema permits it), so the v4 step leaves no row un-backfilled.
            await self._migrate_legacy_columns()
            await self._backfill_validity()
            await self._backfill_workspace()
            version = 4
            await self._set_user_version(version)

        if version < 5:
            # Continuity instrumentation (#378). ``continuity_log`` is in the
            # base schema, so a fresh store already has it; this step exists so
            # an existing v4 store connects without any further migration and
            # simply re-runs the idempotent DDL the connect path applies before
            # migration — recorded here to keep the numbered history complete.
            version = 5
            await self._set_user_version(version)

        if version < 6:
            # Team-memory Phase 2 (#377): recalls become an access-audit
            # substrate by recording the principal and workspace that read.
            # Historical rows predate identity, so the only honest backfill is
            # the pre-Phase-2 degenerate case: default/local/admin.
            await self._migrate_recall_audit()
            await self._migrate_held_workspace()
            version = 6
            await self._set_user_version(version)

        if version < 7:
            # Team Memory collaboration (#377). The v7 tables are created by
            # the idempotent base schema before migrations run; recording the
            # version here makes the upgrade monotonic for existing v6 stores.
            version = 7
            await self._set_user_version(version)

        if version < 8:
            # Semantic decision conflict candidates (#377). The v8 table is
            # additive and created by the idempotent base schema before this
            # marker advances the store version.
            version = 8
            await self._set_user_version(version)

        if version < 9:
            # Team scheduler (#377): existing handoff rows gain capability,
            # priority and accepting-session metadata without rewriting data.
            await self._migrate_team_handoff_scheduler()
            version = 9
            await self._set_user_version(version)

        self.schema_version = version

    @staticmethod
    def _fts_query(text: str) -> str:
        """Convert free text into a safe FTS5 prefix query."""
        tokens = re.findall(r"\w+", text, flags=re.UNICODE)
        return " AND ".join(f"{token}*" for token in tokens[:20])

    async def runtime_status(self) -> dict:
        """Return operational SQLite state for doctor/diagnostics."""
        values: dict[str, object] = {
            "busy_timeout_ms": self.busy_timeout_ms,
            "schema_version": self.schema_version,
            "schema_current": CURRENT_SCHEMA_VERSION,
            "fts5_available": self.fts5_available,
        }
        for key, pragma in (
            ("journal_mode", "PRAGMA journal_mode"),
            ("foreign_keys", "PRAGMA foreign_keys"),
            ("synchronous", "PRAGMA synchronous"),
        ):
            cursor = await self.conn.execute(pragma)
            row = await cursor.fetchone()
            await cursor.close()
            values[key] = row[0] if row else None
        return values

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._connection is not None, "Database not connected. Call connect() first."
        return self._connection

    async def data_version(self) -> int:
        """SQLite's own cross-connection change counter for this database file.

        ``PRAGMA data_version`` changes whenever ANY *other* connection commits —
        including a different process — but a connection's own commits never
        bump its own view of it (verified empirically, not just per the SQLite
        docs). That is exactly "did a peer write since I last checked," for
        the cost of one fast pragma query: no polling thread, no IPC, no
        schema change. Used to invalidate MemoryEngine's process-local
        vector_store/short_term caches when a live peer (another engine
        instance sharing this file, in-process or in a separate process)
        writes without this connection knowing.
        """
        cursor = await self.conn.execute("PRAGMA data_version")
        row = await cursor.fetchone()
        await cursor.close()
        return int(row[0])

    async def close(self) -> None:
        if self._connection:
            await self._connection.close()
            self._connection = None

    async def commit(self) -> None:
        """Commit, timing how long the write lock took to acquire (issue #145).

        The lock-wait histogram is the only metric that cannot be observed at
        the route boundary: contention is a property of the SQLite write lock,
        not of the request, and the wait is invisible in the request latency
        when writes are rare. ``commit()`` is the single funnel every write
        group uses, so timing it here counts each write exactly once.
        """
        started = time.perf_counter()
        try:
            await self.conn.commit()
        finally:
            metrics.observe("levh_db_lock_wait_seconds", time.perf_counter() - started)

    def __getattr__(self, name: str):
        """Resolve former mixin methods against the composed query groups.

        Keeps every historical ``db.<method>()`` call site working without
        this facade re-listing ~79 methods. Attribute lookup reaches here
        only after normal instance/class lookup fails, so facade-owned names
        (conn, connect, commit, close, ...) always win.
        """
        if name.startswith("__"):
            raise AttributeError(name)
        for group in self.__dict__.get("_groups", ()):
            # Walk the group's MRO: its own __dict__ only holds methods the
            # subclass defines; inherited ones live on the base class.
            for klass in type(group).__mro__:
                attr = klass.__dict__.get(name)
                if attr is not None:
                    return attr.__get__(group)
        raise AttributeError(
            f"{type(self).__name__!r} object has no attribute {name!r}"
        )
