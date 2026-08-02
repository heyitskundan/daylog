from datetime import timedelta
from pathlib import Path

from daylog.config import Config, StorageConfig
from daylog.retention import purge
from daylog.util import iso, utcnow


def _make_files(shots_dir, day, name, kb=50):
    d = shots_dir / day
    d.mkdir(parents=True, exist_ok=True)
    full = d / f"{name}.jpg"
    thumb = d / f"{name}.thumb.jpg"
    full.write_bytes(b"x" * kb * 1024)
    thumb.write_bytes(b"t" * 4 * 1024)
    return str(full), str(thumb)


def _insert(conn, ts, full, thumb, deleted=0):
    conn.execute(
        "INSERT INTO screenshots(ts, path, thumb_path, source, ocr_done, deleted) "
        "VALUES(?,?,?, 'auto', 1, ?)",
        (ts, full, thumb, deleted),
    )
    conn.commit()


def test_purge_thins_old_raw_keeps_thumb_and_recent(tmp_path, conn):
    cfg = Config(storage=StorageConfig(data_dir=tmp_path, screenshot_retention_days=14))
    shots = cfg.screenshots_dir

    old_ts = iso(utcnow() - timedelta(days=30))
    recent_ts = iso(utcnow() - timedelta(days=1))

    old_full, old_thumb = _make_files(shots, "2026-05-24", "old", kb=80)
    _insert(conn, old_ts, old_full, old_thumb)

    rec_full, rec_thumb = _make_files(shots, "2026-06-22", "recent", kb=80)
    _insert(conn, recent_ts, rec_full, rec_thumb)

    stats = purge(conn, cfg)

    assert not Path(old_full).exists()
    assert Path(old_thumb).exists()
    prow = conn.execute("SELECT purged FROM screenshots WHERE path=?", (old_full,)).fetchone()
    assert prow["purged"] == 1

    assert Path(rec_full).exists()
    rrow = conn.execute("SELECT purged FROM screenshots WHERE path=?", (rec_full,)).fetchone()
    assert rrow["purged"] == 0

    assert stats["raw_purged"] == 1
    assert stats["mb_freed"] > 0


def test_purge_cleans_soft_deleted_files(tmp_path, conn):
    cfg = Config(storage=StorageConfig(data_dir=tmp_path, screenshot_retention_days=14))
    shots = cfg.screenshots_dir
    recent_ts = iso(utcnow() - timedelta(days=1))

    del_full, del_thumb = _make_files(shots, "2026-06-22", "deleted", kb=30)
    _insert(conn, recent_ts, del_full, del_thumb, deleted=1)

    stats = purge(conn, cfg)

    assert not Path(del_full).exists()
    assert not Path(del_thumb).exists()
    assert stats["deleted_cleaned"] == 1


def test_purge_is_idempotent(tmp_path, conn):
    cfg = Config(storage=StorageConfig(data_dir=tmp_path, screenshot_retention_days=14))
    shots = cfg.screenshots_dir
    old_ts = iso(utcnow() - timedelta(days=30))
    old_full, old_thumb = _make_files(shots, "2026-05-24", "old", kb=80)
    _insert(conn, old_ts, old_full, old_thumb)

    purge(conn, cfg)
    stats2 = purge(conn, cfg)

    assert stats2["raw_purged"] == 0
    assert stats2["deleted_cleaned"] == 0


def test_purge_removes_empty_day_directories(tmp_path, conn):
    cfg = Config(storage=StorageConfig(data_dir=tmp_path, screenshot_retention_days=14))
    shots = cfg.screenshots_dir
    old_ts = iso(utcnow() - timedelta(days=30))
    old_full, old_thumb = _make_files(shots, "2026-05-24", "old", kb=80)
    _insert(conn, old_ts, old_full, old_thumb, deleted=1)

    stats = purge(conn, cfg)
    assert stats["empty_dirs_removed"] == 1
    assert not (shots / "2026-05-24").exists()
