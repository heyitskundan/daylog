"""Validate retention/auto-purge: old raw images thinned, thumbs+recent kept, soft-deletes cleaned."""

import tempfile
from datetime import timedelta
from pathlib import Path

from daylog import db
from daylog.config import Config, StorageConfig
from daylog.retention import purge
from daylog.util import iso, utcnow

tmp = Path(tempfile.mkdtemp())
cfg = Config(storage=StorageConfig(data_dir=tmp, screenshot_retention_days=14))
conn = db.init_db(cfg.db_path)
shots = cfg.screenshots_dir


def make_files(day: str, name: str, kb: int = 50):
    d = shots / day
    d.mkdir(parents=True, exist_ok=True)
    full = d / f"{name}.jpg"
    thumb = d / f"{name}.thumb.jpg"
    full.write_bytes(b"x" * kb * 1024)
    thumb.write_bytes(b"t" * 4 * 1024)
    return str(full), str(thumb)


def insert(ts, full, thumb, deleted=0):
    conn.execute(
        "INSERT INTO screenshots(ts, path, thumb_path, source, ocr_done, deleted) "
        "VALUES(?,?,?, 'auto', 1, ?)",
        (ts, full, thumb, deleted),
    )
    conn.commit()


old_ts = iso(utcnow() - timedelta(days=30))
recent_ts = iso(utcnow() - timedelta(days=1))

old_full, old_thumb = make_files("2026-05-24", "old", kb=80)
insert(old_ts, old_full, old_thumb)

rec_full, rec_thumb = make_files("2026-06-22", "recent", kb=80)
insert(recent_ts, rec_full, rec_thumb)

del_full, del_thumb = make_files("2026-06-22", "deleted", kb=30)
insert(recent_ts, del_full, del_thumb, deleted=1)

stats = purge(conn, cfg)
print("stats:", stats)

# Old raw image gone, thumbnail kept, row marked purged.
assert not Path(old_full).exists(), "old full image should be deleted"
assert Path(old_thumb).exists(), "old thumbnail should be kept"
prow = conn.execute("SELECT purged FROM screenshots WHERE path=?", (old_full,)).fetchone()
assert prow["purged"] == 1, "old row should be marked purged"

# Recent untouched.
assert Path(rec_full).exists(), "recent full image should be kept"
rrow = conn.execute("SELECT purged FROM screenshots WHERE path=?", (rec_full,)).fetchone()
assert rrow["purged"] == 0, "recent row should not be purged"

# Soft-deleted files removed.
assert not Path(del_full).exists() and not Path(del_thumb).exists(), "deleted files should be gone"

assert stats["raw_purged"] == 1, stats
assert stats["deleted_cleaned"] == 1, stats
assert stats["mb_freed"] > 0, stats

# Idempotent: second run does nothing.
stats2 = purge(conn, cfg)
assert stats2["raw_purged"] == 0 and stats2["deleted_cleaned"] == 0, stats2

print("OK: retention thins old raw images, keeps thumbnails + recent, cleans soft-deletes, idempotent")
