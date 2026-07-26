"""SQLite Database Layer — Zero-ops persistence for LEVH."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiosqlite

from server.core.env import get_env

_DEFAULT_DB_PATH = os.getenv("SQLITE_DB_PATH", "./stackmemory.db")
CURRENT_SCHEMA_VERSION = 2
DEFAULT_BUSY_TIMEOUT_MS = 5_000

# ── Schema ────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id          TEXT PRIMARY KEY,
    content     TEXT NOT NULL,
    memory_type TEXT NOT NULL DEFAULT 'short_term',
    embedding   TEXT,                -- JSON-encoded float[]
    importance  REAL DEFAULT 0.5,
    frequency   INTEGER DEFAULT 1,
    tags        TEXT,                -- JSON-encoded string[]
    session_id  TEXT,
    project     TEXT,
    source      TEXT,
    pinned      INTEGER DEFAULT 0,
    metadata    TEXT,                -- JSON-encoded object
    hscore      REAL,
    created_at  TEXT NOT NULL,
    accessed_at TEXT NOT NULL,
    decay_factor REAL DEFAULT 1.0,
    stability_hours REAL DEFAULT 168.0,
    recall_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sessions (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL DEFAULT 'Untitled Session',
    status       TEXT NOT NULL DEFAULT 'active',
    metadata     TEXT,
    memory_count INTEGER DEFAULT 0,
    created_at   TEXT NOT NULL,
    ended_at     TEXT
);

-- Connector framework v2: per-source incremental-sync bookkeeping.
CREATE TABLE IF NOT EXISTS connector_sync (
    source_key     TEXT PRIMARY KEY,   -- "<connector>:<project>" identity
    connector      TEXT NOT NULL,
    project        TEXT,
    last_synced_at TEXT,
    last_fetched   INTEGER DEFAULT 0,
    last_stored    INTEGER DEFAULT 0,
    total_stored   INTEGER DEFAULT 0,
    runs           INTEGER DEFAULT 0
);

-- Entity knowledge graph (persistent): typed entities + memory↔entity links.
CREATE TABLE IF NOT EXISTS entities (
    id         TEXT PRIMARY KEY,       -- "<type>:<key>"
    type       TEXT NOT NULL,          -- person|organization|event|document|task
    ekey       TEXT NOT NULL,
    name       TEXT NOT NULL,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS memory_entities (
    memory_id  TEXT NOT NULL,
    entity_id  TEXT NOT NULL,
    role       TEXT,
    PRIMARY KEY (memory_id, entity_id)
);

-- Deterministic conflict CANDIDATES (review signal, never a verdict).
CREATE TABLE IF NOT EXISTS memory_conflict_candidates (
    id                  TEXT PRIMARY KEY,   -- "<memA>|<memB>" (sorted)
    memory_id_a         TEXT NOT NULL,
    memory_id_b         TEXT NOT NULL,
    shared_entities_json TEXT NOT NULL,
    signal_type         TEXT NOT NULL,
    confidence          REAL NOT NULL,
    status              TEXT NOT NULL,      -- open|dismissed|confirmed|resolved
    explanation_json    TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    reviewed_at         TEXT
);

-- Provenance / trust scores (deterministic reliability signal, NOT truth).
CREATE TABLE IF NOT EXISTS memory_trust_scores (
    memory_id           TEXT PRIMARY KEY,
    confidence          REAL NOT NULL,
    source_score        REAL NOT NULL,
    corroboration_score REAL NOT NULL,
    review_score        REAL NOT NULL,
    recency_score       REAL NOT NULL,
    risk_penalty        REAL NOT NULL,
    label               TEXT NOT NULL,
    computed_at         TEXT NOT NULL,
    breakdown_json      TEXT NOT NULL
);

"""

# Indexes are created AFTER migrations so they can reference columns that
# older databases gain via ALTER TABLE.
_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_mem_session ON memories(session_id);
CREATE INDEX IF NOT EXISTS idx_mem_type    ON memories(memory_type);
CREATE INDEX IF NOT EXISTS idx_mem_hscore  ON memories(hscore);
CREATE INDEX IF NOT EXISTS idx_mem_created ON memories(created_at);
CREATE INDEX IF NOT EXISTS idx_mem_project ON memories(project);
CREATE INDEX IF NOT EXISTS idx_mem_source  ON memories(source);
-- Matches the default list/search ordering (pinned DESC, created_at DESC).
CREATE INDEX IF NOT EXISTS idx_mem_pinned_created ON memories(pinned, created_at);
CREATE INDEX IF NOT EXISTS idx_ses_status  ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_ent_type     ON entities(type);
CREATE INDEX IF NOT EXISTS idx_me_entity     ON memory_entities(entity_id);
CREATE INDEX IF NOT EXISTS idx_me_memory     ON memory_entities(memory_id);
"""

# Columns added after v1.0 — applied to pre-existing databases on connect.
_MIGRATIONS: list[tuple[str, str]] = [
    ("project", "ALTER TABLE memories ADD COLUMN project TEXT"),
    ("source", "ALTER TABLE memories ADD COLUMN source TEXT"),
    ("pinned", "ALTER TABLE memories ADD COLUMN pinned INTEGER DEFAULT 0"),
    ("stability_hours", "ALTER TABLE memories ADD COLUMN stability_hours REAL DEFAULT 168.0"),
    ("recall_count", "ALTER TABLE memories ADD COLUMN recall_count INTEGER DEFAULT 0"),
]


_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    memory_id UNINDEXED,
    content,
    tokenize = 'unicode61'
);

CREATE TRIGGER IF NOT EXISTS memories_fts_ai
AFTER INSERT ON memories BEGIN
    DELETE FROM memories_fts WHERE memory_id = new.id;
    INSERT INTO memories_fts(memory_id, content) VALUES (new.id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS memories_fts_au
AFTER UPDATE OF id, content ON memories BEGIN
    DELETE FROM memories_fts WHERE memory_id = old.id;
    DELETE FROM memories_fts WHERE memory_id = new.id;
    INSERT INTO memories_fts(memory_id, content) VALUES (new.id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS memories_fts_ad
AFTER DELETE ON memories BEGIN
    DELETE FROM memories_fts WHERE memory_id = old.id;
END;
"""


class Database:
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

    async def create_safety_backup(self, destination: str | None = None) -> str | None:
        """Create a consistent SQLite safety copy before destructive restore.

        Uses SQLite's online backup API, so WAL pages are included correctly.
        In-memory databases have no durable location and therefore return None.
        """
        if self.db_path == ":memory:":
            return None

        source = Path(self.db_path).expanduser().resolve()
        if destination:
            target = Path(destination).expanduser().resolve()
        else:
            configured_dir = get_env("LEVH_SAFETY_BACKUP_DIR", "").strip()
            backup_dir = (
                Path(configured_dir).expanduser().resolve()
                if configured_dir
                else source.parent / "safety-backups"
            )
            backup_dir.mkdir(parents=True, exist_ok=True)
            try:
                backup_dir.chmod(0o700)
            except OSError:
                pass
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
            target = backup_dir / f"stackmemory-pre-restore-{stamp}.db"

        target.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(str(target)) as destination_conn:
            await self.conn.backup(destination_conn)
        try:
            target.chmod(0o600)
        except OSError:
            pass
        return str(target)

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

    async def insert_memory(self, memory: dict) -> None:
        await self.conn.execute(
            """
            INSERT OR REPLACE INTO memories
                (id, content, memory_type, embedding, importance, frequency,
                 tags, session_id, project, source, pinned, metadata, hscore,
                 created_at, accessed_at, decay_factor, stability_hours, recall_count)
            VALUES
                (:id, :content, :memory_type, :embedding, :importance, :frequency,
                 :tags, :session_id, :project, :source, :pinned, :metadata, :hscore,
                 :created_at, :accessed_at, :decay_factor, :stability_hours, :recall_count)
            """,
            {
                **memory,
                "embedding": json.dumps(memory.get("embedding")) if memory.get("embedding") else None,
                "tags": json.dumps(memory.get("tags", [])),
                "metadata": json.dumps(memory.get("metadata", {})),
                "pinned": 1 if memory.get("pinned") else 0,
            },
        )
        await self.conn.commit()

    async def get_memory(self, memory_id: str) -> Optional[dict]:
        cursor = await self.conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,))
        row = await cursor.fetchone()
        await cursor.close()
        return self._row_to_memory(row) if row else None

    async def get_all_memories(self, limit: int = 10000) -> list[dict]:
        cursor = await self.conn.execute(
            "SELECT * FROM memories ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [self._row_to_memory(r) for r in rows]

    async def search_memories(
        self,
        memory_type: Optional[str] = None,
        session_id: Optional[str] = None,
        project: Optional[str] = None,
        source: Optional[str] = None,
        tag: Optional[str] = None,
        pinned: Optional[bool] = None,
        min_importance: Optional[float] = None,
        content_like: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        fts_query = self._fts_query(content_like or "") if content_like else ""
        use_fts = bool(content_like and fts_query and self.fts5_available)
        query = (
            "SELECT memories.* FROM memories "
            "JOIN memories_fts ON memories_fts.memory_id = memories.id WHERE 1=1"
            if use_fts
            else "SELECT memories.* FROM memories WHERE 1=1"
        )
        params: list = []

        if memory_type:
            query += " AND memories.memory_type = ?"
            params.append(memory_type)
        if session_id:
            query += " AND memories.session_id = ?"
            params.append(session_id)
        if project:
            query += " AND memories.project = ?"
            params.append(project)
        if source:
            query += " AND memories.source = ?"
            params.append(source)
        if pinned is not None:
            query += " AND memories.pinned = ?"
            params.append(1 if pinned else 0)
        if min_importance is not None:
            query += " AND memories.importance >= ?"
            params.append(min_importance)
        if tag:
            query += " AND memories.tags LIKE ?"
            params.append(f'%"{tag}"%')
        if content_like:
            if use_fts:
                query += " AND memories_fts MATCH ?"
                params.append(fts_query)
            else:
                query += " AND memories.content LIKE ?"
                params.append(f"%{content_like}%")

        if use_fts:
            query += (
                " ORDER BY memories.pinned DESC, bm25(memories_fts), "
                "memories.created_at DESC LIMIT ? OFFSET ?"
            )
        else:
            query += (
                " ORDER BY memories.pinned DESC, memories.created_at DESC LIMIT ? OFFSET ?"
            )
        params.extend([limit, offset])

        cursor = await self.conn.execute(query, params)
        rows = await cursor.fetchall()
        await cursor.close()
        return [self._row_to_memory(r) for r in rows]

    async def update_memory(self, memory_id: str, updates: dict) -> bool:
        sets = []
        params = []
        for key, val in updates.items():
            if key in ("embedding", "tags", "metadata"):
                val = json.dumps(val) if val is not None else None
            elif key == "pinned":
                val = 1 if val else 0
            sets.append(f"{key} = ?")
            params.append(val)
        if not sets:
            return False
        params.append(memory_id)
        cursor = await self.conn.execute(
            f"UPDATE memories SET {', '.join(sets)} WHERE id = ?", params
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def delete_memory(self, memory_id: str) -> bool:
        cursor = await self.conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        await self.conn.commit()
        return cursor.rowcount > 0

    async def clear_all_memories(self) -> int:
        """Delete every memory. Used by a replace-mode restore."""
        cursor = await self.conn.execute("DELETE FROM memories")
        await self.conn.commit()
        return cursor.rowcount

    async def clear_all_sessions(self) -> int:
        """Delete every session. Used by a replace-mode restore."""
        cursor = await self.conn.execute("DELETE FROM sessions")
        await self.conn.commit()
        return cursor.rowcount

    async def delete_memory_cascade(self, memory_id: str) -> bool:
        """Delete a memory and every derived row that references it.

        The operation is one SQLite transaction so a failed hard-delete cannot
        report success while entity, trust or conflict residues survive.
        Orphan entity rows are pruned after their final link disappears.
        """
        await self.conn.execute("BEGIN IMMEDIATE")
        try:
            await self.conn.execute(
                "DELETE FROM memory_conflict_candidates "
                "WHERE memory_id_a = ? OR memory_id_b = ?",
                (memory_id, memory_id),
            )
            await self.conn.execute(
                "DELETE FROM memory_trust_scores WHERE memory_id = ?",
                (memory_id,),
            )
            await self.conn.execute(
                "DELETE FROM memory_entities WHERE memory_id = ?",
                (memory_id,),
            )
            cursor = await self.conn.execute(
                "DELETE FROM memories WHERE id = ?", (memory_id,)
            )
            await self.conn.execute(
                "DELETE FROM entities WHERE id NOT IN "
                "(SELECT DISTINCT entity_id FROM memory_entities)"
            )
            await self.conn.commit()
            return cursor.rowcount > 0
        except Exception:
            await self.conn.rollback()
            raise

    async def memory_residue(self, memory_id: str) -> dict:
        """Return row counts for all persistent layers referencing a memory."""
        checks = {
            "episodic": ("SELECT COUNT(*) FROM memories WHERE id = ?", (memory_id,)),
            "entity_links": (
                "SELECT COUNT(*) FROM memory_entities WHERE memory_id = ?",
                (memory_id,),
            ),
            "trust_score": (
                "SELECT COUNT(*) FROM memory_trust_scores WHERE memory_id = ?",
                (memory_id,),
            ),
            "conflict_candidates": (
                "SELECT COUNT(*) FROM memory_conflict_candidates "
                "WHERE memory_id_a = ? OR memory_id_b = ?",
                (memory_id, memory_id),
            ),
        }
        out = {}
        for key, (sql, params) in checks.items():
            cursor = await self.conn.execute(sql, params)
            row = await cursor.fetchone()
            await cursor.close()
            out[key] = int(row[0] if row else 0)
        return out

    async def restore_snapshot_transaction(
        self, memories: list[dict], sessions: list[dict], replace: bool
    ) -> None:
        """Atomically merge or replace a fully validated snapshot.

        Callers must validate every record before entering this method.  No
        destructive clear occurs until all validation has succeeded.
        """
        await self.conn.execute("BEGIN IMMEDIATE")
        try:
            memory_ids = [str(m["id"]) for m in memories]
            if replace:
                await self.conn.execute("DELETE FROM memory_conflict_candidates")
                await self.conn.execute("DELETE FROM memory_trust_scores")
                await self.conn.execute("DELETE FROM memory_entities")
                await self.conn.execute("DELETE FROM entities")
                await self.conn.execute("DELETE FROM memories")
                await self.conn.execute("DELETE FROM sessions")
            elif memory_ids:
                placeholders = ",".join("?" for _ in memory_ids)
                await self.conn.execute(
                    f"DELETE FROM memory_conflict_candidates "
                    f"WHERE memory_id_a IN ({placeholders}) OR memory_id_b IN ({placeholders})",
                    [*memory_ids, *memory_ids],
                )
                await self.conn.execute(
                    f"DELETE FROM memory_trust_scores WHERE memory_id IN ({placeholders})",
                    memory_ids,
                )
                await self.conn.execute(
                    f"DELETE FROM memory_entities WHERE memory_id IN ({placeholders})",
                    memory_ids,
                )

            for session in sessions:
                await self.conn.execute(
                    """
                    INSERT OR REPLACE INTO sessions
                        (id, name, status, metadata, memory_count, created_at, ended_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session["id"],
                        session.get("name", ""),
                        session.get("status", "active"),
                        json.dumps(session.get("metadata", {}) or {}),
                        int(session.get("memory_count", 0)),
                        session.get("created_at"),
                        session.get("ended_at"),
                    ),
                )

            for memory in memories:
                await self.conn.execute(
                    """
                    INSERT OR REPLACE INTO memories
                        (id, content, memory_type, embedding, importance, frequency,
                         tags, session_id, project, source, pinned, metadata, hscore,
                         created_at, accessed_at, decay_factor, stability_hours, recall_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        memory["id"],
                        memory["content"],
                        str(memory.get("memory_type", "short_term")),
                        json.dumps(memory.get("embedding")) if memory.get("embedding") else None,
                        float(memory.get("importance", 0.5)),
                        int(memory.get("frequency", 1)),
                        json.dumps(memory.get("tags", []) or []),
                        memory.get("session_id"),
                        memory.get("project"),
                        memory.get("source"),
                        1 if memory.get("pinned") else 0,
                        json.dumps(memory.get("metadata", {}) or {}),
                        memory.get("hscore"),
                        memory["created_at"],
                        memory["accessed_at"],
                        float(memory.get("decay_factor", 1.0)),
                        float(memory.get("stability_hours", 168.0)),
                        int(memory.get("recall_count", 0)),
                    ),
                )

            # Session counts are derived from actual restored rows, never
            # trusted from a potentially stale snapshot field.
            await self.conn.execute(
                """
                UPDATE sessions
                SET memory_count = (
                    SELECT COUNT(*) FROM memories WHERE memories.session_id = sessions.id
                )
                """
            )
            await self.conn.execute(
                "DELETE FROM entities WHERE id NOT IN "
                "(SELECT DISTINCT entity_id FROM memory_entities)"
            )
            await self.conn.commit()
        except Exception:
            await self.conn.rollback()
            raise

    async def count_memories(self, memory_type: Optional[str] = None) -> int:
        if memory_type:
            cursor = await self.conn.execute("SELECT COUNT(*) FROM memories WHERE memory_type = ?", (memory_type,))
        else:
            cursor = await self.conn.execute("SELECT COUNT(*) FROM memories")
        row = await cursor.fetchone()
        await cursor.close()
        return row[0]

    async def count_pinned(self) -> int:
        cursor = await self.conn.execute("SELECT COUNT(*) FROM memories WHERE pinned = 1")
        row = await cursor.fetchone()
        await cursor.close()
        return row[0]

    async def embedding_dimension_counts(self) -> dict[int, int]:
        """Count stored vectors by dimension for doctor/migration warnings."""
        cursor = await self.conn.execute(
            "SELECT embedding FROM memories WHERE embedding IS NOT NULL"
        )
        rows = await cursor.fetchall()
        await cursor.close()
        counts: dict[int, int] = {}
        for row in rows:
            try:
                dimension = len(json.loads(row[0]))
            except Exception:
                dimension = -1
            counts[dimension] = counts.get(dimension, 0) + 1
        return counts

    async def memory_aggregates(self) -> dict:
        """Aggregate stats over all persisted memories in one query."""
        cursor = await self.conn.execute(
            "SELECT COUNT(*), AVG(importance), AVG(hscore) FROM memories"
        )
        row = await cursor.fetchone()
        await cursor.close()
        return {
            "count": row[0] or 0,
            "avg_importance": row[1] or 0.0,
            "avg_hscore": row[2] or 0.0,
        }

    async def list_projects(self) -> list[dict]:
        """Distinct projects with memory counts, most recent first."""
        cursor = await self.conn.execute(
            """
            SELECT project, COUNT(*) as count, MAX(created_at) as last_used
            FROM memories
            WHERE project IS NOT NULL AND project != ''
            GROUP BY project
            ORDER BY last_used DESC
            """
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [
            {"name": r[0], "memory_count": r[1], "last_used": r[2]} for r in rows
        ]

    async def list_sources(self) -> list[dict]:
        """Distinct sources (AI clients/tools) with memory counts."""
        cursor = await self.conn.execute(
            """
            SELECT source, COUNT(*) as count, MAX(created_at) as last_used
            FROM memories
            WHERE source IS NOT NULL AND source != ''
            GROUP BY source
            ORDER BY count DESC
            """
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [
            {"name": r[0], "memory_count": r[1], "last_used": r[2]} for r in rows
        ]

    async def list_tags(self) -> list[dict]:
        """All tags with usage counts (tags are stored as JSON arrays)."""
        cursor = await self.conn.execute(
            "SELECT tags FROM memories WHERE tags IS NOT NULL AND tags != '[]'"
        )
        rows = await cursor.fetchall()
        await cursor.close()
        counts: dict[str, int] = {}
        for (raw,) in rows:
            try:
                for tag in json.loads(raw):
                    counts[tag] = counts.get(tag, 0) + 1
            except (json.JSONDecodeError, TypeError):
                continue
        return [
            {"name": name, "count": count}
            for name, count in sorted(counts.items(), key=lambda kv: -kv[1])
        ]

    async def count_sessions(self, status: Optional[str] = None) -> int:
        if status:
            cursor = await self.conn.execute("SELECT COUNT(*) FROM sessions WHERE status = ?", (status,))
        else:
            cursor = await self.conn.execute("SELECT COUNT(*) FROM sessions")
        row = await cursor.fetchone()
        await cursor.close()
        return row[0]

    # ── Session CRUD ──────────────────────────────────────────────

    async def insert_session(self, session: dict) -> None:
        await self.conn.execute(
            """
            INSERT OR REPLACE INTO sessions
                (id, name, status, metadata, memory_count, created_at, ended_at)
            VALUES
                (:id, :name, :status, :metadata, :memory_count, :created_at, :ended_at)
            """,
            {**session, "metadata": json.dumps(session.get("metadata", {}))},
        )
        await self.conn.commit()

    async def get_session(self, session_id: str) -> Optional[dict]:
        cursor = await self.conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
        row = await cursor.fetchone()
        await cursor.close()
        return self._row_to_session(row) if row else None

    async def get_all_sessions(self, limit: int = 100) -> list[dict]:
        cursor = await self.conn.execute(
            "SELECT * FROM sessions ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [self._row_to_session(r) for r in rows]

    async def count_session_memories(self, session_id: str) -> int:
        cursor = await self.conn.execute(
            "SELECT COUNT(*) FROM memories WHERE session_id = ?", (session_id,)
        )
        row = await cursor.fetchone()
        await cursor.close()
        return row[0]

    async def update_session(self, session_id: str, updates: dict) -> bool:
        sets = []
        params = []
        for key, val in updates.items():
            if key == "metadata":
                val = json.dumps(val) if val is not None else None
            sets.append(f"{key} = ?")
            params.append(val)
        if not sets:
            return False
        params.append(session_id)
        cursor = await self.conn.execute(
            f"UPDATE sessions SET {', '.join(sets)} WHERE id = ?", params
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    # ── Connector sync state (v2) ─────────────────────────────────

    async def get_sync_state(self, source_key: str) -> Optional[dict]:
        cursor = await self.conn.execute(
            "SELECT * FROM connector_sync WHERE source_key = ?", (source_key,)
        )
        row = await cursor.fetchone()
        await cursor.close()
        return dict(row) if row else None

    async def list_sync_states(self) -> list[dict]:
        cursor = await self.conn.execute(
            "SELECT * FROM connector_sync ORDER BY last_synced_at DESC"
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(r) for r in rows]

    async def record_sync(
        self,
        source_key: str,
        connector: str,
        project: Optional[str],
        last_synced_at: str,
        fetched: int,
        stored: int,
    ) -> None:
        """Upsert a connector's sync bookkeeping, accumulating totals/runs."""
        await self.conn.execute(
            """
            INSERT INTO connector_sync
                (source_key, connector, project, last_synced_at,
                 last_fetched, last_stored, total_stored, runs)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(source_key) DO UPDATE SET
                connector      = excluded.connector,
                project        = excluded.project,
                last_synced_at = excluded.last_synced_at,
                last_fetched   = excluded.last_fetched,
                last_stored    = excluded.last_stored,
                total_stored   = connector_sync.total_stored + excluded.last_stored,
                runs           = connector_sync.runs + 1
            """,
            (source_key, connector, project, last_synced_at, fetched, stored, stored),
        )
        await self.conn.commit()

    # ── Entity knowledge graph ────────────────────────────────────

    async def clear_entity_graph(self) -> None:
        await self.conn.execute("DELETE FROM memory_entities")
        await self.conn.execute("DELETE FROM entities")
        await self.conn.commit()

    async def upsert_entity(
        self, entity_id: str, etype: str, ekey: str, name: str, now: str
    ) -> None:
        """Insert an entity or keep the most descriptive (longest) name."""
        await self.conn.execute(
            """
            INSERT INTO entities (id, type, ekey, name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = CASE WHEN length(excluded.name) > length(entities.name)
                            THEN excluded.name ELSE entities.name END,
                updated_at = excluded.updated_at
            """,
            (entity_id, etype, ekey, name, now, now),
        )

    async def link_memory_entity(
        self, memory_id: str, entity_id: str, role: str | None
    ) -> None:
        await self.conn.execute(
            "INSERT OR IGNORE INTO memory_entities (memory_id, entity_id, role) VALUES (?, ?, ?)",
            (memory_id, entity_id, role),
        )

    async def commit(self) -> None:
        await self.conn.commit()

    async def list_entities(
        self, etype: Optional[str] = None, limit: int = 200
    ) -> list[dict]:
        """Entities with their memory mention counts, most-mentioned first."""
        base = """
            SELECT e.id, e.type, e.ekey, e.name, e.updated_at,
                   COUNT(me.memory_id) AS mentions
            FROM entities e
            LEFT JOIN memory_entities me ON me.entity_id = e.id
        """
        params: tuple = ()
        if etype:
            base += " WHERE e.type = ?"
            params = (etype,)
        base += " GROUP BY e.id ORDER BY mentions DESC, e.name ASC LIMIT ?"
        params = params + (limit,)
        cursor = await self.conn.execute(base, params)
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(r) for r in rows]

    async def get_entity_row(self, entity_id: str) -> Optional[dict]:
        cursor = await self.conn.execute(
            """
            SELECT e.id, e.type, e.ekey, e.name, e.updated_at,
                   COUNT(me.memory_id) AS mentions
            FROM entities e
            LEFT JOIN memory_entities me ON me.entity_id = e.id
            WHERE e.id = ?
            GROUP BY e.id
            """,
            (entity_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return dict(row) if row else None

    async def find_entity(self, query: str, etype: Optional[str] = None) -> Optional[str]:
        """Resolve free text to an entity id: exact id, then name/key substring."""
        q = query.strip().lower()
        if not q:
            return None
        exact = await self.get_entity_row(q if ":" in q else "")
        if exact:
            return exact["id"]
        sql = "SELECT id FROM entities WHERE (lower(name) LIKE ? OR ekey LIKE ?)"
        params: tuple = (f"%{q}%", f"%{q}%")
        if etype:
            sql += " AND type = ?"
            params = params + (etype,)
        sql += " LIMIT 1"
        cursor = await self.conn.execute(sql, params)
        row = await cursor.fetchone()
        await cursor.close()
        return row[0] if row else None

    async def entity_memory_ids(self, entity_id: str, limit: int = 100) -> list[str]:
        cursor = await self.conn.execute(
            "SELECT memory_id FROM memory_entities WHERE entity_id = ? LIMIT ?",
            (entity_id, limit),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [r[0] for r in rows]

    async def entity_neighbors(self, entity_id: str, limit: int = 20) -> list[dict]:
        """Entities that co-occur with ``entity_id`` in the same memories,
        ranked by how many memories they share."""
        cursor = await self.conn.execute(
            """
            SELECT e.id, e.type, e.name, COUNT(*) AS shared
            FROM memory_entities a
            JOIN memory_entities b ON a.memory_id = b.memory_id AND b.entity_id != a.entity_id
            JOIN entities e ON e.id = b.entity_id
            WHERE a.entity_id = ?
            GROUP BY b.entity_id
            ORDER BY shared DESC, e.name ASC
            LIMIT ?
            """,
            (entity_id, limit),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(r) for r in rows]

    async def entity_type_counts(self) -> dict:
        cursor = await self.conn.execute(
            "SELECT type, COUNT(*) FROM entities GROUP BY type"
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return {r[0]: r[1] for r in rows}

    # ── Provenance / trust scores ─────────────────────────────────

    async def upsert_trust(self, row: dict) -> None:
        await self.conn.execute(
            """
            INSERT INTO memory_trust_scores
                (memory_id, confidence, source_score, corroboration_score,
                 review_score, recency_score, risk_penalty, label,
                 computed_at, breakdown_json)
            VALUES
                (:memory_id, :confidence, :source_score, :corroboration_score,
                 :review_score, :recency_score, :risk_penalty, :label,
                 :computed_at, :breakdown_json)
            ON CONFLICT(memory_id) DO UPDATE SET
                confidence=excluded.confidence,
                source_score=excluded.source_score,
                corroboration_score=excluded.corroboration_score,
                review_score=excluded.review_score,
                recency_score=excluded.recency_score,
                risk_penalty=excluded.risk_penalty,
                label=excluded.label,
                computed_at=excluded.computed_at,
                breakdown_json=excluded.breakdown_json
            """,
            row,
        )

    async def get_trust(self, memory_id: str) -> Optional[dict]:
        cursor = await self.conn.execute(
            "SELECT * FROM memory_trust_scores WHERE memory_id = ?", (memory_id,)
        )
        r = await cursor.fetchone()
        await cursor.close()
        return dict(r) if r else None

    async def list_low_trust(self, threshold: float = 0.4, limit: int = 50) -> list[dict]:
        cursor = await self.conn.execute(
            """
            SELECT * FROM memory_trust_scores
            WHERE confidence < ?
            ORDER BY confidence ASC
            LIMIT ?
            """,
            (threshold, limit),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(r) for r in rows]

    async def clear_trust(self) -> None:
        await self.conn.execute("DELETE FROM memory_trust_scores")
        await self.conn.commit()

    # ── Conflict candidates ───────────────────────────────────────

    async def get_conflict(self, conflict_id: str) -> Optional[dict]:
        cursor = await self.conn.execute(
            "SELECT * FROM memory_conflict_candidates WHERE id = ?", (conflict_id,)
        )
        r = await cursor.fetchone()
        await cursor.close()
        return dict(r) if r else None

    async def insert_conflict_if_absent(self, row: dict) -> bool:
        """Insert a new candidate only if the pair isn't already recorded (so a
        re-run never resets a dismissed/confirmed candidate to open). Returns
        True if a new row was inserted."""
        existing = await self.get_conflict(row["id"])
        if existing is not None:
            return False
        await self.conn.execute(
            """
            INSERT INTO memory_conflict_candidates
                (id, memory_id_a, memory_id_b, shared_entities_json, signal_type,
                 confidence, status, explanation_json, created_at, reviewed_at)
            VALUES
                (:id, :memory_id_a, :memory_id_b, :shared_entities_json, :signal_type,
                 :confidence, :status, :explanation_json, :created_at, NULL)
            """,
            row,
        )
        return True

    async def list_conflicts(self, status: Optional[str] = None, limit: int = 100) -> list[dict]:
        if status:
            cursor = await self.conn.execute(
                "SELECT * FROM memory_conflict_candidates WHERE status = ? "
                "ORDER BY confidence DESC, created_at DESC LIMIT ?",
                (status, limit),
            )
        else:
            cursor = await self.conn.execute(
                "SELECT * FROM memory_conflict_candidates "
                "ORDER BY confidence DESC, created_at DESC LIMIT ?",
                (limit,),
            )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(r) for r in rows]

    async def update_conflict_status(
        self, conflict_id: str, status: str, reviewed_at: str
    ) -> bool:
        cursor = await self.conn.execute(
            "UPDATE memory_conflict_candidates SET status = ?, reviewed_at = ? WHERE id = ?",
            (status, reviewed_at, conflict_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def delete_conflict(self, conflict_id: str) -> bool:
        cursor = await self.conn.execute(
            "DELETE FROM memory_conflict_candidates WHERE id = ?", (conflict_id,)
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def delete_conflicts_for_memory_ids(self, memory_ids: list[str]) -> int:
        """Remove derived conflict rows that reference deleted memories."""
        ids = [str(mid) for mid in memory_ids if mid]
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        cursor = await self.conn.execute(
            f"DELETE FROM memory_conflict_candidates "
            f"WHERE memory_id_a IN ({placeholders}) OR memory_id_b IN ({placeholders})",
            [*ids, *ids],
        )
        await self.conn.commit()
        return cursor.rowcount

    async def conflicts_for_memory(self, memory_id: str) -> list[dict]:
        cursor = await self.conn.execute(
            "SELECT * FROM memory_conflict_candidates "
            "WHERE memory_id_a = ? OR memory_id_b = ?",
            (memory_id, memory_id),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(r) for r in rows]

    # ── Helpers ──────────────────────────────────────────────────

    @staticmethod
    def _row_to_memory(row: aiosqlite.Row) -> dict:
        d = dict(row)
        for field in ("embedding", "tags", "metadata"):
            raw = d.get(field)
            if raw:
                d[field] = json.loads(raw)
            else:
                d[field] = [] if field == "tags" else ({} if field == "metadata" else None)
        d["pinned"] = bool(d.get("pinned"))
        return d

    @staticmethod
    def _row_to_session(row: aiosqlite.Row) -> dict:
        d = dict(row)
        raw = d.get("metadata")
        d["metadata"] = json.loads(raw) if raw else {}
        return d
