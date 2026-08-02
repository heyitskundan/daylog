"""Built-in watcher: the Windows APIs read the desktop, and the Recorder turns samples into
events rows the segmenter can consume. Run: uv run python scripts/test_watcher.py"""

import sys
import tempfile
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from daylog import db, watcher
from daylog.util import iso, parse_iso, utcnow

# 1. The native reads work against the live desktop.
app, title, hwnd = watcher.foreground_window()
assert hwnd, "no foreground window handle"
assert app, "could not read the foreground process name"
idle = watcher.idle_seconds()
assert idle >= 0, "idle seconds should never be negative"
print(f"OK: reads the desktop natively (app={app!r}, idle={idle:.0f}s)")

# 2. The Recorder turns samples into window/afk/web rows with real durations.
conn = db.init_db(Path(tempfile.mkdtemp()) / "t.db")
rec = watcher.Recorder(conn, idle_threshold=120)
t0 = utcnow()
at = lambda secs: iso(t0 + timedelta(seconds=secs))

S = watcher.CurrentState
rec.sample(S(app="Code.exe", title="a.py - proj", url=None), now=at(0))
rec.sample(S(app="Code.exe", title="a.py - proj", url=None), now=at(10))
# switching windows closes the first stretch and opens the next
rec.sample(S(app="chrome.exe", title="docs", url="https://docs.python.org/x"), now=at(20))
rec.sample(S(app="chrome.exe", title="docs", url="https://docs.python.org/x"), now=at(30))
# going away closes the open window/tab and opens an afk stretch
rec.sample(S(app="chrome.exe", title="docs", afk=True, idle_secs=130), now=at(160))
rec.sample(S(app="chrome.exe", title="docs", afk=True, idle_secs=200), now=at(230))
rec.flush()

rows = conn.execute(
    "SELECT source, app, title, url, ts_start, ts_end, extra FROM events ORDER BY ts_start"
).fetchall()
by_source = {}
for r in rows:
    by_source.setdefault(r["source"], []).append(r)

assert "window" in by_source, "no window events recorded"
assert "web" in by_source, "no web events recorded"
assert "afk" in by_source, "no afk event recorded"

win = by_source["window"]
assert win[0]["app"] == "Code.exe" and win[0]["title"] == "a.py - proj"
dur = (parse_iso(win[0]["ts_end"]) - parse_iso(win[0]["ts_start"])).total_seconds()
assert 9 <= dur <= 11, f"first window stretch should span ~10s, got {dur}"

web = by_source["web"][0]
assert web["url"] == "https://docs.python.org/x"

afk = by_source["afk"][0]
assert '"status": "afk"' in (afk["extra"] or ""), "afk row must carry the status the segmenter reads"
# The away stretch is backdated to when input actually stopped, not when we noticed.
away_start = parse_iso(afk["ts_start"])
assert away_start < parse_iso(at(160)), "afk should start when input stopped, not at detection"
print(f"OK: Recorder writes window/web/afk rows with real spans ({len(rows)} events)")

# 3. A long stretch is checkpointed, so the timeline is never stale and a crash costs little.
conn2 = db.init_db(Path(tempfile.mkdtemp()) / "t2.db")
rec2 = watcher.Recorder(conn2, idle_threshold=120)
for sec in range(0, 200, 10):
    rec2.sample(S(app="Code.exe", title="long.py"), now=at(sec))
n_open = conn2.execute("SELECT COUNT(*) FROM events WHERE source='window'").fetchone()[0]
assert n_open >= 2, f"a 200s stretch should be checkpointed into >=2 rows, got {n_open}"
print(f"OK: long stretches are checkpointed ({n_open} rows for 200s in one window)")

# 4. The segmenter re-merges those checkpoints back into one block.
from daylog.config import Config
from daylog.segment import segment_day
from daylog.util import local_date_str

rec2.flush()
cfg = Config()
segs = segment_day(conn2, cfg, local_date_str())
assert len(segs) == 1, f"checkpointed rows should merge into one segment, got {len(segs)}"
assert segs[0].primary_app == "Code.exe"
print("OK: the segmenter merges checkpointed rows back into a single block")

print("\nALL WATCHER CHECKS PASSED")
