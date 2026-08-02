"""SQLite timeline store.

One database holds the whole timeline. Screenshots are files on disk referenced by row.
All timestamps are stored as ISO-8601 UTC strings (sortable, timezone-safe).
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Normalized timeline rows from every source.
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY,
    ts_start   TEXT NOT NULL,
    ts_end     TEXT,
    source     TEXT NOT NULL,           -- window | web | afk | screenshot | terminal
                                        -- (aw-* variants are pre-2026-07 rows from ActivityWatch)
    app        TEXT,
    title      TEXT,
    url        TEXT,
    file_path  TEXT,
    extra      TEXT                      -- JSON blob for source-specific fields
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts_start);
CREATE INDEX IF NOT EXISTS idx_events_source ON events(source);

-- Captured screenshots. Raw image + thumbnail live on disk.
CREATE TABLE IF NOT EXISTS screenshots (
    id           INTEGER PRIMARY KEY,
    ts           TEXT NOT NULL,
    path         TEXT NOT NULL,
    thumb_path   TEXT,
    app          TEXT,
    title        TEXT,
    source       TEXT NOT NULL DEFAULT 'auto',  -- auto | switch | manual
    ocr_text     TEXT,
    ocr_done     INTEGER NOT NULL DEFAULT 0,
    embedding_id INTEGER,
    deleted      INTEGER NOT NULL DEFAULT 0,
    purged       INTEGER NOT NULL DEFAULT 0   -- full image deleted by retention; thumb kept
);
CREATE INDEX IF NOT EXISTS idx_shots_ts ON screenshots(ts);
CREATE INDEX IF NOT EXISTS idx_shots_ocr_done ON screenshots(ocr_done);

-- Commands reconstructed from shell history.
CREATE TABLE IF NOT EXISTS terminal_cmds (
    id        INTEGER PRIMARY KEY,
    ts        TEXT,
    shell     TEXT,
    cwd       TEXT,
    command   TEXT NOT NULL,
    exit_code INTEGER,
    UNIQUE(ts, command, shell)
);

-- Deterministic time blocks produced by the segmenter.
CREATE TABLE IF NOT EXISTS segments (
    id           INTEGER PRIMARY KEY,
    ts_start     TEXT NOT NULL,
    ts_end       TEXT NOT NULL,
    primary_app  TEXT,
    project_guess TEXT,
    url_host     TEXT,
    n_switches   INTEGER NOT NULL DEFAULT 0,
    idle_secs    INTEGER NOT NULL DEFAULT 0,
    active_secs  INTEGER NOT NULL DEFAULT 0,
    signals      TEXT                      -- JSON: struggle/learn heuristic signals
);
CREATE INDEX IF NOT EXISTS idx_segments_ts ON segments(ts_start);

-- Confirmed semantic tasks (after AI proposal + user edit).
CREATE TABLE IF NOT EXISTS tasks (
    id             INTEGER PRIMARY KEY,
    date           TEXT NOT NULL,
    name           TEXT NOT NULL,
    summary        TEXT,
    total_secs     INTEGER NOT NULL DEFAULT 0,
    struggled      INTEGER NOT NULL DEFAULT 0,
    learned        INTEGER NOT NULL DEFAULT 0,
    status         TEXT NOT NULL DEFAULT 'proposed',  -- proposed | confirmed
    steps          TEXT,                  -- JSON: [{label, ts_start, ts_end, secs}]
    screenshot_ids TEXT                   -- JSON: [id, ...]
);
CREATE INDEX IF NOT EXISTS idx_tasks_date ON tasks(date);

-- End-of-day report + where it was published in the vault.
CREATE TABLE IF NOT EXISTS daily_reports (
    date         TEXT PRIMARY KEY,
    markdown     TEXT,
    generated_by TEXT,                    -- ollama | claude | manual
    vault_path   TEXT,
    published_at TEXT
);

-- Bookkeeping for incremental AW polling (last-seen timestamp per bucket).
CREATE TABLE IF NOT EXISTS sync_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after a DB was first created (CREATE IF NOT EXISTS won't)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(screenshots)").fetchall()}
    if "purged" not in cols:
        conn.execute("ALTER TABLE screenshots ADD COLUMN purged INTEGER NOT NULL DEFAULT 0")
        conn.commit()


def init_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    _migrate(conn)
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def get_sync_state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_sync_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO sync_state(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
