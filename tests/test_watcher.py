from datetime import timedelta

from daylog import db, watcher
from daylog.config import Config
from daylog.segment import segment_day
from daylog.util import iso, local_date_str, parse_iso, utcnow

S = watcher.CurrentState


def test_recorder_writes_window_web_and_afk_rows_with_real_spans(tmp_path):
    conn = db.init_db(tmp_path / "t.db")
    rec = watcher.Recorder(conn, idle_threshold=120)
    t0 = utcnow()
    at = lambda secs: iso(t0 + timedelta(seconds=secs))

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

    assert "window" in by_source
    assert "web" in by_source
    assert "afk" in by_source

    win = by_source["window"]
    assert win[0]["app"] == "Code.exe" and win[0]["title"] == "a.py - proj"
    dur = (parse_iso(win[0]["ts_end"]) - parse_iso(win[0]["ts_start"])).total_seconds()
    assert 9 <= dur <= 11

    web = by_source["web"][0]
    assert web["url"] == "https://docs.python.org/x"

    afk = by_source["afk"][0]
    assert '"status": "afk"' in (afk["extra"] or "")
    # The away stretch is backdated to when input actually stopped, not when we noticed.
    away_start = parse_iso(afk["ts_start"])
    assert away_start < parse_iso(at(160))


def test_recorder_checkpoints_long_stretches(tmp_path):
    conn = db.init_db(tmp_path / "t2.db")
    rec = watcher.Recorder(conn, idle_threshold=120)
    t0 = utcnow()
    at = lambda secs: iso(t0 + timedelta(seconds=secs))

    for sec in range(0, 200, 10):
        rec.sample(S(app="Code.exe", title="long.py"), now=at(sec))

    n_open = conn.execute("SELECT COUNT(*) FROM events WHERE source='window'").fetchone()[0]
    assert n_open >= 2


def test_segmenter_remerges_checkpointed_recorder_rows(tmp_path):
    conn = db.init_db(tmp_path / "t3.db")
    rec = watcher.Recorder(conn, idle_threshold=120)
    t0 = utcnow()
    at = lambda secs: iso(t0 + timedelta(seconds=secs))

    for sec in range(0, 200, 10):
        rec.sample(S(app="Code.exe", title="long.py"), now=at(sec))
    rec.flush()

    cfg = Config()
    segs = segment_day(conn, cfg, local_date_str())
    assert len(segs) == 1
    assert segs[0].primary_app == "Code.exe"


def test_recorder_ignores_zero_length_stretch(tmp_path):
    conn = db.init_db(tmp_path / "t4.db")
    rec = watcher.Recorder(conn, idle_threshold=120)
    now = iso(utcnow())
    rec.sample(S(app="Code.exe", title="a.py"), now=now)
    rec.flush()
    rows = conn.execute("SELECT * FROM events").fetchall()
    assert rows == []
