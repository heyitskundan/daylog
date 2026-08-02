"""Validate the dashboard task edit/confirm + publish (P3.4) against a live server."""

import json
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from daylog import db
import daylog.config as C
from daylog.config import Config, ObsidianConfig, StorageConfig
from daylog.segment import segment_day
from daylog.util import iso, local_date_str

tmp = Path(tempfile.mkdtemp())
vault = tmp / "vault"; vault.mkdir()
cfg = Config(storage=StorageConfig(data_dir=tmp),
             obsidian=ObsidianConfig(vault_path=vault, auto_detect=False))
C.get_config = lambda: cfg          # web.serve_background reads this
conn = db.init_db(cfg.db_path)

base = datetime.now().astimezone().replace(hour=10, minute=0, second=0, microsecond=0)
date_str = local_date_str(base)
for i in range(12):
    s = base + timedelta(minutes=i); e = s + timedelta(seconds=60)
    conn.execute("INSERT INTO events(ts_start,ts_end,source,app,title,extra) VALUES(?,?,?,?,?,?)",
                 (iso(s.astimezone(timezone.utc)), iso(e.astimezone(timezone.utc)),
                  "aw-window", "Code.exe", "x.py - valuelyne - VS Code", json.dumps({"duration": 60})))
conn.commit()
segs = segment_day(conn, cfg, date_str)

# Insert a proposed task (as the labeler would).
steps = json.dumps({"ai_steps": ["edit x.py"], "timed": [
    {"label": "valuelyne", "app": "Code.exe", "host": None, "secs": segs[0].active_secs,
     "ts_start": segs[0].ts_start, "ts_end": segs[0].ts_end}], "note": ""})
conn.execute("INSERT INTO tasks(date,name,summary,total_secs,struggled,learned,status,steps,screenshot_ids) "
             "VALUES(?,?,?,?,0,0,'proposed',?, '[]')",
             (date_str, "Untitled work", "did stuff", segs[0].active_secs, steps))
conn.commit()

# 1. journal renders the task as a narrative entry + publish; edit mode has the task form.
from daylog.web import day_stats, render_day
html = render_day(day_stats(conn, date_str))
assert "Untitled work" in html, "task title should appear as a journal entry"
assert "Publish this day" in html          # Obsidian is an optional destination, not the label
assert "Re-summarize" in html
assert f'href="/note/{date_str}"' in html, "the day should link to its published note"
edit_html = render_day(day_stats(conn, date_str), edit_mode=True)
assert 'action="/api/task"' in edit_html, "edit mode should expose the task form"
print("OK: journal renders entries + publish; edit mode renders task form")

# 2. live server: POST task edit, then publish.
from daylog import web
server, url = web.serve_background(port=8790)
try:
    time.sleep(0.3)
    tid = conn.execute("SELECT id FROM tasks WHERE date=?", (date_str,)).fetchone()["id"]

    def post(path, fields):
        data = urllib.parse.urlencode(fields).encode()
        req = urllib.request.Request(url.rstrip("/") + path, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status

    s = post("/api/task", {"id": tid, "date": date_str, "name": "Set up Cloudflare",
                           "summary": "tunnel", "struggled": "on"})
    assert s == 200, s
    # Re-read on a fresh connection (the server wrote via its own connection).
    c2 = db.connect(cfg.db_path)
    row = c2.execute("SELECT name,status,struggled FROM tasks WHERE id=?", (tid,)).fetchone()
    assert row["name"] == "Set up Cloudflare", row["name"]
    assert row["status"] == "confirmed", row["status"]
    assert row["struggled"] == 1, row["struggled"]
    print("OK: POST /api/task renamed + confirmed + struggled flag")

    s = post("/api/publish", {"date": date_str})
    assert s == 200, s
    daily = vault / "Activity" / date_str[:7] / date_str / f"{date_str}.md"
    assert daily.exists(), list((vault / "Activity").rglob("*"))
    assert "Set up Cloudflare" in daily.read_text(encoding="utf-8")
    print("OK: POST /api/publish wrote the daily note to Obsidian")
finally:
    server.shutdown()

print("\nALL P3.4 DASHBOARD CHECKS PASSED")
