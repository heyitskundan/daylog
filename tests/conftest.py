from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from daylog import db
from daylog.config import Config, StorageConfig
from daylog.util import iso, local_date_str


@pytest.fixture
def cfg(tmp_path):
    return Config(storage=StorageConfig(data_dir=tmp_path))


@pytest.fixture
def conn(cfg):
    return db.init_db(cfg.db_path)


@pytest.fixture
def base_time():
    """A stable local 'now' anchored to today at 10:00, so date_str always matches."""
    return datetime.now().astimezone().replace(hour=10, minute=0, second=0, microsecond=0)


@pytest.fixture
def date_str(base_time):
    return local_date_str(base_time)


def add_window_event(conn, base, off_min, dur_sec, app, title, source="aw-window"):
    start = base + timedelta(minutes=off_min)
    end = start + timedelta(seconds=dur_sec)
    conn.execute(
        "INSERT INTO events(ts_start, ts_end, source, app, title, extra) VALUES(?,?,?,?,?,?)",
        (
            iso(start.astimezone(timezone.utc)), iso(end.astimezone(timezone.utc)),
            source, app, title, json.dumps({"duration": dur_sec}),
        ),
    )


def add_web_event(conn, base, off_min, url, source="aw-web"):
    start = base + timedelta(minutes=off_min)
    conn.execute(
        "INSERT INTO events(ts_start, ts_end, source, url, extra) VALUES(?,?,?,?,?)",
        (
            iso(start.astimezone(timezone.utc)), iso(start.astimezone(timezone.utc)),
            source, url, "{}",
        ),
    )


def add_afk_event(conn, base, off_min, dur_sec, source="aw-afk"):
    start = base + timedelta(minutes=off_min)
    end = start + timedelta(seconds=dur_sec)
    conn.execute(
        "INSERT INTO events(ts_start, ts_end, source, extra) VALUES(?,?,?,?)",
        (
            iso(start.astimezone(timezone.utc)), iso(end.astimezone(timezone.utc)),
            source, json.dumps({"status": "afk"}),
        ),
    )
