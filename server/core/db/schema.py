"""The SQLite schema, its indexes and the migration table.

Kept apart from the connection logic: this is data, and it is the part a
reader looking for "what columns exist" actually wants.
"""

from __future__ import annotations

import os



_DEFAULT_DB_PATH = os.getenv("SQLITE_DB_PATH", "./stackmemory.db")


CURRENT_SCHEMA_VERSION = 2


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

-- Mistake guard: one row per recorded mistake. The rule itself lives in
-- `memories` (pinned, so it never decays); this table is the incident log
-- that says which rule was learned, from what, and whether it still stands.
CREATE TABLE IF NOT EXISTS violations (
    id           TEXT PRIMARY KEY,
    rule_id      TEXT NOT NULL,          -- memories.id of the pinned rule
    task         TEXT,                   -- what was being attempted
    wrong_action TEXT NOT NULL,          -- what was done
    root_cause   TEXT,                   -- why it happened
    tool_name    TEXT,                   -- tool involved, when known
    severity     TEXT NOT NULL DEFAULT 'medium',
    source       TEXT NOT NULL DEFAULT 'user',
    occurred_at  TEXT NOT NULL,
    resolved     INTEGER DEFAULT 0,
    resolution   TEXT
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

-- Files attached to a memory as evidence, not content. The memory stays text
-- (so decay/H(x,psi) keep working); the file lives on disk and is referenced
-- by path + hash. derived_text is what recall actually searches — OCR/
-- transcript/caption text, populated by an optional local extractor or typed
-- in by hand. status flips to 'missing'/'changed' when a verify pass finds
-- the file gone or its hash no longer matches, which is also what raises a
-- conflict candidate (see server.core.engine.attachments).
CREATE TABLE IF NOT EXISTS attachments (
    id           TEXT PRIMARY KEY,
    memory_id    TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    path         TEXT NOT NULL,
    sha256       TEXT NOT NULL,
    mime         TEXT,
    size         INTEGER NOT NULL,
    derived_text TEXT,
    derived_by   TEXT NOT NULL DEFAULT 'none',   -- tesseract|whisper|manual|none
    status       TEXT NOT NULL DEFAULT 'ok',     -- ok|missing|changed
    created_at   TEXT NOT NULL,
    verified_at  TEXT
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

-- Candidates the admission gate answered "review" for: near-duplicates that
-- are redundant but not identical, which the gate deliberately declines to
-- decide on its own. "review" has always meant "hold for a human" -- this is
-- the place that holds them. Without it the verdict had no store behind it and
-- the content was dropped, which is the one thing a memory layer must not do
-- quietly.
--
-- These are NOT memories. They are unadmitted candidates: no embedding, no
-- hscore, no decay. They never appear in recall, and one becomes a memory only
-- when a human admits it.
CREATE TABLE IF NOT EXISTS held_memories (
    id                 TEXT PRIMARY KEY,
    content            TEXT NOT NULL,
    importance         REAL NOT NULL,
    tags_json          TEXT NOT NULL,
    session_id         TEXT,
    project            TEXT,
    source             TEXT,
    memory_type        TEXT NOT NULL,
    pinned             INTEGER NOT NULL DEFAULT 0,
    metadata_json      TEXT NOT NULL,
    reasons_json       TEXT NOT NULL,
    max_similarity     REAL NOT NULL,
    status             TEXT NOT NULL DEFAULT 'held',  -- held|admitted|discarded
    created_at         TEXT NOT NULL,
    decided_at         TEXT,
    admitted_memory_id TEXT
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
CREATE INDEX IF NOT EXISTS idx_viol_rule     ON violations(rule_id);
CREATE INDEX IF NOT EXISTS idx_viol_when     ON violations(occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_attach_memory ON attachments(memory_id);
-- The queue is read as "what is still waiting", newest first.
CREATE INDEX IF NOT EXISTS idx_held_status ON held_memories(status, created_at DESC);
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
