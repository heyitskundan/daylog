"""Validate that AFK (idle) time is subtracted from a segment's active time."""

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from daylog import db
from daylog.config import Config, StorageConfig
from daylog.segment import segment_day
from daylog.util import iso, local_date_str

tmp = Path(tempfile.mkdtemp())
cfg = Config(storage=StorageConfig(data_dir=tmp))
conn = db.init_db(cfg.db_path)

base = datetime.now().astimezone().replace(hour=10, minute=0, second=0, microsecond=0)
date_str = local_date_str(base)


def add(source, off_start_min, dur_sec, **data):
    s = base + timedelta(minutes=off_start_min)
    e = s + timedelta(seconds=dur_sec)
    conn.execute(
        "INSERT INTO events(ts_start, ts_end, source, app, title, extra) VALUES(?,?,?,?,?,?)",
        (iso(s.astimezone(timezone.utc)), iso(e.astimezone(timezone.utc)), source,
         data.get("app"), data.get("title"), json.dumps(data.get("extra", {}))),
    )


# One window "focused" for 8 hours (AW merged it), but the user was AFK for 7 of those hours.
add("aw-window", 0, 8 * 3600, app="Code.exe", title="main.py - daylog - VS Code")
add("aw-afk", 30, 7 * 3600, extra={"status": "afk"})   # 10:30 -> 17:30 away
conn.commit()

segs = segment_day(conn, cfg, date_str)
assert len(segs) == 1, len(segs)
active_min = segs[0].active_secs / 60
idle_min = segs[0].idle_secs / 60
print(f"active = {active_min:.0f} min, idle = {idle_min:.0f} min (raw focus was 480 min)")

# 8h focus - 7h afk = ~1h active.
assert 55 <= active_min <= 65, f"expected ~60 min active, got {active_min:.0f}"
assert 415 <= idle_min <= 425, f"expected ~420 min idle, got {idle_min:.0f}"
# Active can never exceed the 24h-impossible wall span either.
assert segs[0].active_secs <= 8 * 3600
print("OK: idle (AFK) time is subtracted from active time")
