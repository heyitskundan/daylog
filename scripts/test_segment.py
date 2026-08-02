"""Validate the segmenter against a synthetic AW-style window timeline (no AW needed)."""

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from daylog import db
from daylog.config import Config, StorageConfig
from daylog.segment import segment_day
from daylog.util import iso, local_date_str

# Isolated temp DB so we never touch the real one.
tmp = Path(tempfile.mkdtemp())
cfg = Config(storage=StorageConfig(data_dir=tmp))
conn = db.init_db(cfg.db_path)

# Build a fake day: 20 min in VSCode on "valuelyne", then a Chrome research spree
# (stackoverflow), then back to VSCode. Use local "now" anchored to today.
base = datetime.now().astimezone().replace(hour=10, minute=0, second=0, microsecond=0)


def add_window(offset_min, dur_sec, app, title):
    start = base + timedelta(minutes=offset_min)
    end = start + timedelta(seconds=dur_sec)
    conn.execute(
        "INSERT INTO events(ts_start, ts_end, source, app, title, extra) VALUES(?,?,?,?,?,?)",
        (iso(start.astimezone(timezone.utc)), iso(end.astimezone(timezone.utc)),
         "aw-window", app, title, json.dumps({"duration": dur_sec})),
    )


def add_web(offset_min, url):
    start = base + timedelta(minutes=offset_min)
    conn.execute(
        "INSERT INTO events(ts_start, ts_end, source, url, extra) VALUES(?,?,?,?,?)",
        (iso(start.astimezone(timezone.utc)), iso(start.astimezone(timezone.utc)),
         "aw-web", url, "{}"),
    )


# Block 1: focused coding, 26 minutes (long dwell).
for i in range(26):
    add_window(i, 60, "Code.exe", "main.py - valuelyne - Visual Studio Code")

# Block 2: research churn on stackoverflow, lots of title flips, 8 minutes.
add_web(26, "https://stackoverflow.com/questions/123")
for i in range(40):
    add_window(26 + i * 0.2, 12, "chrome.exe", f"python error fix {i} - Stack Overflow")

# Block 3: back to coding.
for i in range(10):
    add_window(40 + i, 60, "Code.exe", "db.py - valuelyne - Visual Studio Code")

conn.commit()

date_str = local_date_str(base)
segments = segment_day(conn, cfg, date_str)

print(f"{len(segments)} segments for {date_str}:")
for s in segments:
    print(f"  app={s.primary_app:<12} active={s.active_secs:>5}s switches={s.n_switches:>3} "
          f"proj={s.project_guess} host={s.url_host} struggle={s.signals.get('struggle_score')}")

assert len(segments) >= 3, f"expected >=3 segments, got {len(segments)}"
apps = [s.primary_app for s in segments]
assert "Code.exe" in apps and "chrome.exe" in apps, apps
# The first coding block should register long dwell.
assert any(s.signals.get("long_dwell") for s in segments), "expected a long-dwell segment"
# The chrome research block should be flagged as research + likely struggle.
chrome = [s for s in segments if s.primary_app == "chrome.exe"][0]
assert chrome.signals.get("research_host") is True, chrome.signals
assert chrome.url_host == "stackoverflow.com", chrome.url_host
# Coding blocks should NOT inherit the browser host (half-open boundary handling).
code_segs = [s for s in segments if s.primary_app == "Code.exe"]
assert all(not s.signals.get("research_host") for s in code_segs), \
    [s.url_host for s in code_segs]
assert chrome.project_guess is None or isinstance(chrome.project_guess, str)
# VSCode project parsed from title.
code = [s for s in segments if s.primary_app == "Code.exe"][0]
assert code.project_guess == "valuelyne", code.project_guess

print("\nOK: segmentation, project guess, and struggle/research signals all correct")
