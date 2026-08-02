"""Retention / auto-purge.

After `screenshot_retention_days`, the full-resolution screenshot image is deleted from disk
but its thumbnail and OCR text are kept, so old days stay viewable and searchable without the
disk cost of full frames. Soft-deleted screenshots (deleted=1) have their files removed too.
Empty day-folders are cleaned up.

Runs automatically at the start of `daylog run` and once per day while it loops; also exposed
as `daylog purge`.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path

from . import db
from .config import Config
from .util import iso, utcnow


def _unlink(path: str | None) -> int:
    """Delete a file if present; return bytes freed."""
    if not path:
        return 0
    p = Path(path)
    try:
        size = p.stat().st_size
        p.unlink()
        return size
    except OSError:
        return 0


def purge(conn: sqlite3.Connection, cfg: Config) -> dict:
    """Apply retention. Returns stats dict."""
    days = cfg.storage.screenshot_retention_days
    cutoff = iso(utcnow() - timedelta(days=days))

    raw_purged = 0
    bytes_freed = 0

    # 1. Old, still-present full images -> delete file, keep thumbnail, mark purged.
    rows = conn.execute(
        "SELECT id, path FROM screenshots "
        "WHERE purged = 0 AND deleted = 0 AND ts < ?",
        (cutoff,),
    ).fetchall()
    with db.transaction(conn):
        for r in rows:
            bytes_freed += _unlink(r["path"])
            conn.execute("UPDATE screenshots SET purged = 1 WHERE id = ?", (r["id"],))
            raw_purged += 1

    # 2. Soft-deleted rows: make sure their files are gone (defensive).
    deleted_cleaned = 0
    drows = conn.execute(
        "SELECT id, path, thumb_path FROM screenshots WHERE deleted = 1 AND purged = 0"
    ).fetchall()
    with db.transaction(conn):
        for r in drows:
            freed = _unlink(r["path"]) + _unlink(r["thumb_path"])
            if freed:
                bytes_freed += freed
            conn.execute("UPDATE screenshots SET purged = 1 WHERE id = ?", (r["id"],))
            deleted_cleaned += 1

    empty_dirs = _clean_empty_day_dirs(cfg.screenshots_dir)

    return {
        "retention_days": days,
        "raw_purged": raw_purged,
        "deleted_cleaned": deleted_cleaned,
        "mb_freed": round(bytes_freed / (1024 * 1024), 2),
        "empty_dirs_removed": empty_dirs,
    }


def _clean_empty_day_dirs(screenshots_dir: Path) -> int:
    removed = 0
    if not screenshots_dir.exists():
        return 0
    for day_dir in screenshots_dir.iterdir():
        if day_dir.is_dir():
            try:
                next(day_dir.iterdir())
            except StopIteration:
                try:
                    day_dir.rmdir()
                    removed += 1
                except OSError:
                    pass
    return removed
