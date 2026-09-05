"""SQLite Database Layer — Zero-ops persistence for LEVH."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import aiosqlite

from server.core.env import get_env
from .db.schema import CURRENT_SCHEMA_VERSION, _DEFAULT_DB_PATH, _FTS_SCHEMA, _INDEXES, _MIGRATIONS, _SCHEMA
from .db.aggregates import AggregateQueries
from .db.attachments import AttachmentQueries
from .db.entities import EntityQueries
from .db.findings import FindingQueries
from .db.guard import GuardQueries
from .db.held import HeldMemoryQueries
from .db.memories import MemoryQueries
from .db.sessions import SessionQueries
from .db.snapshot import SnapshotQueries
from .db.trust import TrustQueries

DEFAULT_BUSY_TIMEOUT_MS = 5_000

# ── Schema ────────────────────────────────────────────────────────────







class Database(
    MemoryQueries,
    AggregateQueries,
    SessionQueries,
    EntityQueries,
    TrustQueries,
    GuardQueries,
    SnapshotQueries,
    AttachmentQueries,
    HeldMemoryQueries,
    FindingQueries,
):
    """Async SQLite wrapper with auto-init."""

    def __init__(self, db_path: str = _DEFAULT_DB_PATH):
        self.db_path = db_path
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

        `PRAGMA data_version` changes whenever ANY *other* connection commits —
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

    # ── Memory CRUD ───────────────────────────────────────────────




















    # ── Session CRUD ──────────────────────────────────────────────






    # ── Connector sync state (v2) ─────────────────────────────────




    # ── Mistake guard ─────────────────────────────────────────────




    # ── Entity knowledge graph ────────────────────────────────────




    async def commit(self) -> None:
        await self.conn.commit()







    # ── Provenance / trust scores ─────────────────────────────────






    # ── Conflict candidates ───────────────────────────────────────








    # ── Helpers ──────────────────────────────────────────────────


