"""Validate dashboard data aggregation + HTML rendering (no server, no external deps)."""

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from daylog import db
from daylog.config import Config, StorageConfig
from daylog.segment import segment_day
from daylog.util import iso, local_date_str
from daylog.web import day_stats, render_day, render_search, search_ocr

tmp = Path(tempfile.mkdtemp())
cfg = Config(storage=StorageConfig(data_dir=tmp))
conn = db.init_db(cfg.db_path)

base = datetime.now().astimezone().replace(hour=9, minute=0, second=0, microsecond=0)
date_str = local_date_str(base)


def add_window(off_min, dur, app, title):
    s = base + timedelta(minutes=off_min)
    e = s + timedelta(seconds=dur)
    conn.execute(
        "INSERT INTO events(ts_start, ts_end, source, app, title, extra) VALUES(?,?,?,?,?,?)",
        (iso(s.astimezone(timezone.utc)), iso(e.astimezone(timezone.utc)),
         "aw-window", app, title, json.dumps({"duration": dur})),
    )


for i in range(15):
    add_window(i, 60, "Code.exe", "app.py - valuelyne - Visual Studio Code")
for i in range(10):
    add_window(15 + i, 60, "WindowsTerminal.exe", "cloudflared tunnel - pwsh")
conn.commit()

segment_day(conn, cfg, date_str)

# Two fake screenshots: one auto, one manual (badged).
for i, src in enumerate(["auto", "manual"]):
    ts = iso((base + timedelta(minutes=5 + i)).astimezone(timezone.utc))
    conn.execute(
        "INSERT INTO screenshots(ts, path, thumb_path, app, title, source, ocr_text, ocr_done) "
        "VALUES(?,?,?,?,?,?,?,1)",
        (ts, f"/fake/{i}.jpg", f"/fake/{i}.thumb.jpg", "Code.exe",
         "app.py - valuelyne", src, "cloudflare tunnel token DNS record", ),
    )
conn.commit()

stats = day_stats(conn, date_str)
assert stats["total_active"] > 0, stats
assert len(stats["segments"]) >= 2, len(stats["segments"])
assert len(stats["shots"]) == 2, len(stats["shots"])
apps = dict(stats["by_app"])
assert "Code.exe" in apps and "WindowsTerminal.exe" in apps, apps

html = render_day(stats)                  # no tasks yet -> "not summarized" journal state
assert "Where the time went" in html
assert "Code.exe" in html                 # app shown in the time bars
assert "manual" in html                   # manual screenshot badged in Frames
assert "Summarize this day" in html       # primary action when unlabeled

# OCR search
rows = search_ocr(conn, "cloudflare")
assert len(rows) == 2, len(rows)
shtml = render_search(rows, "cloudflare")
assert "match(es)" in shtml and "cloudflare" in shtml.lower()

# Empty day renders without error
empty = render_day(day_stats(conn, "2000-01-01"))
assert "Nothing captured" in empty

print("OK: dashboard aggregation, rendering, badge, and OCR search all good")
