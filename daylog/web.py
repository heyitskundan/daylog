"""Local dashboard — the daylog "Work Journal" UI (standard-library http.server, no deps).

The day reads as a written journal entry: a serif masthead, a one-line thesis of the day, and
each task as a narrative entry hung off a left "log thread" whose dot turns amber at a struggle
and pine at a learning. One key publishes to Obsidian.

Routes:
  GET  /                  -> redirect to today
  GET  /day/<YYYY-MM-DD>  -> the day's journal  (?edit=1 for the task editor)
  GET  /days              -> archive: every day that has captured data
  GET  /jump?d=...         -> redirect to that day (the date picker in the bar)
  GET  /settings          -> connections + preferences + data folder
  GET  /search?q=...       -> full-text search over OCR'd screenshots
  GET  /img/<id>          -> screenshot (?full=1 for full image)
  POST /api/...            -> task edit, label, publish, capture start/stop, settings, autostart
"""

from __future__ import annotations

import html
import json
import sqlite3
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from . import db
from .config import get_config
from .segment import _local_day_bounds_utc
from .util import human_duration, local_date_str

# ----------------------------------------------------------------------------- data


def day_stats(conn: sqlite3.Connection, date_str: str) -> dict:
    start_utc, end_utc = _local_day_bounds_utc(date_str)

    segments = conn.execute(
        "SELECT * FROM segments WHERE ts_start >= ? AND ts_start < ? ORDER BY ts_start",
        (start_utc, end_utc),
    ).fetchall()

    by_app: dict[str, int] = {}
    total_active = 0
    for s in segments:
        total_active += s["active_secs"]
        by_app[s["primary_app"] or "?"] = by_app.get(s["primary_app"] or "?", 0) + s["active_secs"]

    shots = conn.execute(
        "SELECT id, ts, app, title, source FROM screenshots "
        "WHERE ts >= ? AND ts < ? AND deleted = 0 ORDER BY ts",
        (start_utc, end_utc),
    ).fetchall()

    tasks = conn.execute(
        "SELECT * FROM tasks WHERE date = ? ORDER BY total_secs DESC", (date_str,)
    ).fetchall()
    entries = _task_entries(conn, tasks)

    return {
        "date": date_str,
        "segments": segments,
        "by_app": sorted(by_app.items(), key=lambda x: -x[1]),
        "total_active": total_active,
        "shots": shots,
        "tasks": tasks,
        "entries": entries,
        "lede": _day_lede(total_active, entries),
    }


def _task_entries(conn: sqlite3.Connection, tasks) -> list[dict]:
    """Turn task rows into display-ready narrative entries (sorted by start time).

    Output is sanitised again at render time (belt-and-suspenders) so even tasks written by an
    older build or a weak model never display leaked field names or runaway number sequences.
    """
    from .label import _clean_name, _clean_steps, _clean_text, _is_degenerate

    out = []
    for t in tasks:
        steps = json.loads(t["steps"] or "{}")
        timed = steps.get("timed") or []
        starts = [x["ts_start"] for x in timed if x.get("ts_start")]
        ends = [x.get("ts_end") or x.get("ts_start") for x in timed if x.get("ts_start")]
        host = next((x["host"] for x in timed if x.get("host")), None)
        commands = []
        if starts:
            rows = conn.execute(
                "SELECT command FROM terminal_cmds WHERE ts >= ? AND ts <= ? ORDER BY ts LIMIT 6",
                (min(starts), max(ends)),
            ).fetchall()
            commands = [r["command"] for r in rows]
        raw_note = steps.get("note") or ""
        out.append({
            "id": t["id"],
            "name": _clean_name(t["name"]),
            "secs": t["total_secs"],
            "struggled": bool(t["struggled"]),
            "learned": bool(t["learned"]),
            "status": t["status"],
            "summary": _clean_text(t["summary"] or "", 300),
            "note": "" if _is_degenerate(raw_note) else _clean_text(raw_note, 300),
            "steps": _clean_steps(steps.get("ai_steps") or []),
            "commands": commands,
            "host": host,
            "start": min(starts)[11:16] if starts else "",
            "sort_key": min(starts) if starts else "",
        })
    out.sort(key=lambda e: e["sort_key"])
    return out


def _day_lede(total_secs: int, entries: list[dict]) -> str:
    """A factual one-line thesis for the day, built only from the data (no invention)."""
    if not entries:
        return ""
    n = len(entries)
    top = max(entries, key=lambda e: e["secs"])
    struggles = [e["name"] for e in entries if e["struggled"]]
    learned = [e["name"] for e in entries if e["learned"]]
    parts = [
        f'{n} task{"s" if n != 1 else ""} across {human_duration(total_secs)}.',
        f'Most of it ({human_duration(top["secs"])}) went to <b>{html.escape(top["name"])}</b>.',
    ]
    if struggles:
        parts.append(f'The fight was <span class="s">{html.escape(struggles[0])}</span>.')
    if learned:
        parts.append(f'You came away <span class="l">learning from {html.escape(learned[0])}</span>.')
    return " ".join(parts)


def days_index(conn: sqlite3.Connection) -> list[dict]:
    """Every local day that has anything captured, newest first.

    Timestamps are stored as UTC; SQLite's 'localtime' modifier maps them onto the same local
    calendar days the rest of the app groups by (tasks are already keyed by local date).
    """
    days: dict[str, dict] = {}

    def row(date_str: str) -> dict:
        return days.setdefault(date_str, {
            "date": date_str, "shots": 0, "active": 0, "segments": 0,
            "tasks": 0, "published": False,
        })

    for r in conn.execute(
        "SELECT date(ts,'localtime') d, COUNT(*) n FROM screenshots WHERE deleted = 0 GROUP BY d"
    ):
        if r["d"]:
            row(r["d"])["shots"] = r["n"]
    for r in conn.execute(
        "SELECT date(ts_start,'localtime') d, COUNT(*) n, SUM(active_secs) a FROM segments GROUP BY d"
    ):
        if r["d"]:
            e = row(r["d"])
            e["segments"], e["active"] = r["n"], r["a"] or 0
    for r in conn.execute("SELECT date, COUNT(*) n FROM tasks GROUP BY date"):
        if r["date"]:
            row(r["date"])["tasks"] = r["n"]
    for r in conn.execute("SELECT date FROM daily_reports WHERE published_at IS NOT NULL"):
        if r["date"]:
            row(r["date"])["published"] = True

    return sorted(days.values(), key=lambda e: e["date"], reverse=True)


def neighbor_days(conn: sqlite3.Connection, date_str: str) -> tuple[str, str]:
    """The nearest days with data on either side of `date_str`.

    Capture is bursty — there can be week-long gaps — so stepping one calendar day at a time
    walks through empty pages. Fall back to the adjacent calendar day when there is nothing
    captured in that direction (so you can still reach an untouched day, e.g. tomorrow).
    """
    dates = [d["date"] for d in days_index(conn)]  # newest first
    older = next((d for d in dates if d < date_str), None)
    newer = next((d for d in reversed(dates) if d > date_str), None)
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    prev = older or (dt - timedelta(days=1)).strftime("%Y-%m-%d")
    nxt = newer or (dt + timedelta(days=1)).strftime("%Y-%m-%d")
    return prev, nxt


def search_ocr(conn: sqlite3.Connection, query: str, limit: int = 60) -> list[sqlite3.Row]:
    if not query.strip():
        return []
    like = f"%{query.strip()}%"
    return conn.execute(
        "SELECT id, ts, app, title, source, ocr_text FROM screenshots "
        "WHERE deleted = 0 AND (ocr_text LIKE ? OR title LIKE ?) ORDER BY ts DESC LIMIT ?",
        (like, like, limit),
    ).fetchall()


# -------------------------------------------------------------------------- rendering

_CSS = """
:root{
  --paper:#EAEDF1; --raised:#F3F5F7; --ink:#1B2230; --muted:#5B6573; --faint:#8A93A1;
  --line:#D3D9E0; --amber:#B26A1B; --amber-soft:#E7C79A; --teal:#1F6F5C; --teal-soft:#AFD3C8;
  --serif:"Iowan Old Style","Palatino Linotype",Palatino,"Book Antiqua",Georgia,serif;
  --sans:ui-sans-serif,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  --mono:ui-monospace,"Cascadia Code","SF Mono",Menlo,Consolas,monospace;
}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--sans);
  font-size:16px;line-height:1.6;-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
a{color:var(--ink);text-decoration:none}

.bar{position:sticky;top:0;z-index:5;display:flex;align-items:center;gap:18px;
  padding:13px 28px;background:color-mix(in srgb,var(--paper) 88%,transparent);
  backdrop-filter:saturate(1.1) blur(8px);border-bottom:1px solid var(--line)}
.mark{font-family:var(--mono);font-weight:600;letter-spacing:.14em;text-transform:uppercase;
  font-size:12.5px}
.mark b{color:var(--amber)}
.bar .spacer{flex:1}
.navlinks{display:flex;gap:14px;font-family:var(--mono);font-size:12.5px}
.navlinks a{color:var(--muted)} .navlinks a:hover{color:var(--ink)}
.bar form{margin:0}
input[type=search]{font-family:var(--mono);font-size:12.5px;background:var(--raised);
  border:1px solid var(--line);color:var(--ink);border-radius:8px;padding:6px 11px;width:190px}
.datejump{font-family:var(--mono);font-size:12.5px;background:var(--raised);
  border:1px solid var(--line);color:var(--muted);border-radius:8px;padding:5px 9px}
.datejump:hover{color:var(--ink)}
kbd{font-family:var(--mono);font-size:11px;background:var(--raised);border:1px solid var(--line);
  border-bottom-width:2px;border-radius:5px;padding:1px 6px;color:var(--muted)}

.page{max-width:730px;margin:0 auto;padding:42px 28px 96px}

.eyebrowrow{display:flex;align-items:center;gap:14px;margin:0 0 12px}
.eyebrow{font-family:var(--mono);font-size:12px;letter-spacing:.34em;text-transform:uppercase;
  color:var(--amber);margin:0}
.capchip{margin-left:auto;display:inline-flex;align-items:center;gap:8px;font-family:var(--mono);
  font-size:12px;color:var(--muted)}
.capchip .led{width:8px;height:8px;border-radius:50%}
.capchip form{margin:0}
.capbtn{font-family:var(--mono);font-size:11.5px;background:none;border:1px solid var(--line);
  color:var(--muted);border-radius:99px;padding:3px 10px;cursor:pointer}
.capbtn:hover{color:var(--ink);border-color:var(--muted)}

.masthead{display:flex;justify-content:space-between;align-items:flex-end;gap:24px;
  border-bottom:2px solid var(--ink);padding-bottom:18px}
.date{font-family:var(--serif);font-weight:600;font-size:50px;line-height:.98;
  letter-spacing:-.015em;margin:0}
.date span{display:block;font-size:19px;font-weight:500;color:var(--muted);margin-top:6px}
.stats{text-align:right;font-family:var(--mono);white-space:nowrap}
.stats .big{font-size:29px;font-weight:600;letter-spacing:-.02em}
.stats .sub{font-size:13px;color:var(--muted);margin-top:4px}

.lede{font-family:var(--serif);font-size:22px;line-height:1.5;font-style:italic;
  margin:28px 0 42px;max-width:62ch}
.lede b{font-style:normal;font-weight:600}
.lede .s{font-style:normal;font-weight:600;color:var(--amber);
  box-shadow:inset 0 -.5em 0 color-mix(in srgb,var(--amber) 14%,transparent)}
.lede .l{font-style:normal;font-weight:600;color:var(--teal)}

.log{display:flex;flex-direction:column}
.entry{display:grid;grid-template-columns:62px 1fr}
.entry__time{font-family:var(--mono);font-size:12.5px;color:var(--faint);padding-top:3px}
.entry__body{position:relative;border-left:1.5px solid var(--line);padding:0 0 30px 26px;
  animation:rise .5s cubic-bezier(.2,.7,.2,1) both}
.entry:last-child .entry__body{padding-bottom:6px}
.entry__body::before{content:"";position:absolute;left:-6.5px;top:4px;width:11px;height:11px;
  border-radius:50%;background:var(--paper);border:2.5px solid var(--faint)}
.entry.struggle .entry__body{border-left-color:var(--amber-soft)}
.entry.struggle .entry__body::before{border-color:var(--amber);background:var(--amber)}
.entry.learned:not(.struggle) .entry__body::before{border-color:var(--teal);background:var(--teal)}
.head{display:flex;align-items:baseline;gap:14px}
.title{font-family:var(--serif);font-size:21px;font-weight:600;letter-spacing:-.01em;margin:0;flex:1}
.dur{font-family:var(--mono);font-size:13px;color:var(--muted);white-space:nowrap}
.tags{display:flex;gap:7px;margin:9px 0 0}
.tag{font-family:var(--mono);font-size:11px;letter-spacing:.06em;text-transform:uppercase;
  padding:2px 9px;border-radius:99px;border:1px solid currentColor}
.tag.struggle{color:var(--amber)} .tag.learned{color:var(--teal)} .tag.muted{color:var(--faint)}
.note{margin:11px 0 0}
.steps{margin:12px 0 0;padding:0;list-style:none;color:var(--muted);font-size:14.5px}
.steps li{padding:1px 0 1px 18px;position:relative}
.steps li::before{content:"\\203A";position:absolute;left:2px;color:var(--faint)}
.cmd{display:block;width:fit-content;max-width:100%;overflow-x:auto;font-family:var(--mono);
  font-size:12.5px;background:var(--raised);border:1px solid var(--line);border-radius:6px;
  padding:5px 10px;margin-top:8px;white-space:nowrap}
.cmd::before{content:"$ ";color:var(--amber)}
.host{font-family:var(--mono);font-size:12px;color:var(--faint)}

.section-label{font-family:var(--mono);font-size:12px;letter-spacing:.2em;text-transform:uppercase;
  color:var(--faint);margin:40px 0 16px;display:flex;align-items:center;gap:12px}
.section-label::after{content:"";flex:1;height:1px;background:var(--line)}
.bars{display:flex;flex-direction:column;gap:10px}
.barrow{display:grid;grid-template-columns:120px 1fr 64px;align-items:center;gap:14px}
.barrow .name{font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.barrow .track{height:9px;background:var(--line);border-radius:99px;overflow:hidden}
.barrow .fill{height:100%;border-radius:99px;background:var(--ink);transform-origin:left;
  animation:grow .8s cubic-bezier(.2,.7,.2,1) both}
.barrow.accent .fill{background:var(--amber)}
.barrow .val{font-family:var(--mono);font-size:13px;color:var(--muted);text-align:right}

.actions{margin-top:44px;display:flex;flex-direction:column;gap:14px}
.publish{display:flex;align-items:center;justify-content:center;gap:12px;background:var(--ink);
  color:var(--paper);border:0;border-radius:13px;padding:17px;font-family:var(--sans);font-size:16px;
  font-weight:600;cursor:pointer;transition:transform .12s ease,background .2s ease;width:100%}
.publish:hover{transform:translateY(-1px);background:#11161f}
.publish kbd{background:rgba(255,255,255,.14);border-color:rgba(255,255,255,.25);color:#cfd6df}
.subtle{display:flex;gap:22px;justify-content:center;font-size:14px}
.subtle button,.subtle a{background:none;border:0;font:inherit;color:var(--muted);cursor:pointer;
  border-bottom:1px solid var(--line);padding:0 0 1px}
.subtle button:hover,.subtle a:hover{color:var(--ink);border-color:var(--muted)}

.frames{display:flex;flex-wrap:wrap;gap:12px}
.frame{position:relative;width:200px}
.frame img{width:200px;border-radius:9px;border:1px solid var(--line);display:block}
.frame .badge{position:absolute;top:7px;left:7px;background:var(--amber);color:#fff;font-family:var(--mono);
  font-size:10px;letter-spacing:.05em;padding:2px 7px;border-radius:99px}
.frame .meta{font-family:var(--mono);font-size:11px;color:var(--faint);margin-top:5px;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

.foot{margin-top:40px;text-align:center;font-family:var(--mono);font-size:11.5px;color:var(--faint)}
.foot kbd{margin:0 2px}
.empty{text-align:center;color:var(--muted);padding:70px 20px;font-family:var(--serif);font-size:20px;font-style:italic}
.empty code{font-family:var(--mono);font-size:14px;font-style:normal;background:var(--raised);
  padding:2px 7px;border-radius:6px;border:1px solid var(--line)}
.flash{background:color-mix(in srgb,var(--teal) 14%,var(--paper));border:1px solid var(--teal);
  color:var(--teal);padding:11px 15px;border-radius:10px;margin-bottom:20px;font-size:14.5px}
.flash.err{background:color-mix(in srgb,var(--amber) 14%,var(--paper));border-color:var(--amber);color:var(--amber)}

/* archive */
.month{font-family:var(--mono);font-size:12px;letter-spacing:.2em;text-transform:uppercase;
  color:var(--faint);margin:34px 0 12px;display:flex;align-items:center;gap:12px}
.month::after{content:"";flex:1;height:1px;background:var(--line)}
.daylist{display:flex;flex-direction:column;gap:8px}
.dayrow{display:grid;grid-template-columns:52px 1fr auto;align-items:center;gap:16px;
  background:var(--raised);border:1px solid var(--line);border-radius:11px;padding:13px 16px;
  transition:transform .12s ease,border-color .2s ease}
.dayrow:hover{transform:translateY(-1px);border-color:var(--muted)}
.dayrow.is-today{border-color:var(--amber)}
.dayrow .num{font-family:var(--serif);font-size:26px;font-weight:600;line-height:1;text-align:center}
.dayrow .num small{display:block;font-family:var(--mono);font-size:10px;font-weight:400;
  letter-spacing:.1em;text-transform:uppercase;color:var(--faint);margin-top:4px}
.dayrow .what{min-width:0}
.dayrow .what b{font-weight:600}
.dayrow .sub{font-family:var(--mono);font-size:12px;color:var(--faint);margin-top:3px}
.dayrow .right{text-align:right;font-family:var(--mono);white-space:nowrap}
.dayrow .time{font-size:16px;font-weight:600}
.dayrow .state{font-size:11px;letter-spacing:.06em;text-transform:uppercase;margin-top:4px;color:var(--faint)}
.dayrow .state.pub{color:var(--teal)} .dayrow .state.raw{color:var(--amber)}

.note-md{margin:0;font-family:var(--mono);font-size:13px;line-height:1.65;white-space:pre-wrap;
  word-break:break-word;color:var(--ink)}

/* edit + settings panels */
.panel{background:var(--raised);border:1px solid var(--line);border-radius:13px;padding:20px;margin-bottom:18px}
.panel h2{font-family:var(--mono);font-size:12px;letter-spacing:.16em;text-transform:uppercase;
  color:var(--muted);margin:0 0 14px}
.field{display:flex;flex-direction:column;gap:6px;margin-bottom:14px}
.field label{font-size:13px;color:var(--muted)}
.field input,.field select{font-family:var(--sans);font-size:14.5px;background:var(--paper);
  border:1px solid var(--line);color:var(--ink);border-radius:8px;padding:9px 11px;width:100%}
.field.row{flex-direction:row;align-items:center;gap:9px;margin-bottom:9px}
.field.row input{width:auto}
.twocol{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.btn{font-family:var(--sans);font-size:14.5px;font-weight:600;background:var(--ink);color:var(--paper);
  border:0;border-radius:10px;padding:11px 18px;cursor:pointer}
.btn:hover{background:#11161f}
.btn.ghost{background:none;border:1px solid var(--line);color:var(--ink)}
.btn.ghost:hover{border-color:var(--muted)}
.statline{display:flex;align-items:center;gap:9px;margin:7px 0;font-size:14.5px}
.statline .led{width:9px;height:9px;border-radius:50%}
.statline .det{color:var(--faint);font-family:var(--mono);font-size:12.5px;margin-left:auto}
.tedit{margin-bottom:14px}
.tedit .head{gap:10px}
.tedit input.tname{font-family:var(--serif);font-size:19px;font-weight:600;background:var(--paper);
  border:1px solid var(--line);border-radius:8px;padding:7px 11px;flex:1}
.tedit .chk{font-family:var(--mono);font-size:12px;color:var(--muted);display:flex;align-items:center;gap:5px}
.tedit textarea{width:100%;margin-top:9px;font-family:var(--sans);font-size:14.5px;background:var(--paper);
  border:1px solid var(--line);color:var(--ink);border-radius:8px;padding:9px 11px;resize:vertical;min-height:46px}

@keyframes rise{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
@keyframes grow{from{transform:scaleX(0)}to{transform:scaleX(1)}}
.entry:nth-child(1) .entry__body{animation-delay:.04s}
.entry:nth-child(2) .entry__body{animation-delay:.11s}
.entry:nth-child(3) .entry__body{animation-delay:.18s}
.entry:nth-child(4) .entry__body{animation-delay:.25s}
.entry:nth-child(n+5) .entry__body{animation-delay:.3s}
@media (prefers-reduced-motion:reduce){*{animation:none!important}}
@media (max-width:560px){
  .date{font-size:36px}.lede{font-size:19px}.entry{grid-template-columns:46px 1fr}
  .barrow{grid-template-columns:86px 1fr 56px}.twocol{grid-template-columns:1fr}
  input[type=search]{width:130px}
}
"""

_JS = """
document.addEventListener("keydown",function(e){
  var tag=(e.target.tagName||"").toLowerCase();
  if(tag==="input"||tag==="textarea"||tag==="select")return;
  if(e.key==="Enter"){var f=document.getElementById("publishform");if(f){e.preventDefault();f.submit();}}
  else if(e.key==="/"){var s=document.getElementById("searchbox");if(s){e.preventDefault();s.focus();}}
  else if(e.key==="ArrowLeft"){var p=document.getElementById("navprev");if(p)location.href=p.href;}
  else if(e.key==="ArrowRight"){var n=document.getElementById("navnext");if(n)location.href=n.href;}
});
"""


def _page(title: str, date_str: str, body: str,
          prev: str | None = None, nxt: str | None = None) -> str:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    prev = prev or (dt - timedelta(days=1)).strftime("%Y-%m-%d")
    nxt = nxt or (dt + timedelta(days=1)).strftime("%Y-%m-%d")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title><style>{_CSS}</style></head><body>
<header class="bar">
  <span class="mark">day<b>·</b>log</span>
  <span class="spacer"></span>
  <nav class="navlinks">
    <a id="navprev" href="/day/{prev}" title="Previous day with data">&larr;</a>
    <a href="/day/{local_date_str()}">today</a>
    <a id="navnext" href="/day/{nxt}" title="Next day with data">&rarr;</a>
    <a href="/days">archive</a>
    <a href="/settings">settings</a>
  </nav>
  <form action="/jump" method="get">
    <input class="datejump" type="date" name="d" value="{date_str}"
           title="Jump to a day" onchange="this.form.submit()">
  </form>
  <form action="/search" method="get">
    <input id="searchbox" type="search" name="q" placeholder="search screenshots">
  </form>
</header>
<main class="page">{body}</main>
<script>{_JS}</script>
</body></html>"""


def _capchip(capture_running) -> str:
    if capture_running is None:
        return ('<span class="capchip"><span class="led" style="background:#8A93A1"></span>'
                "managed by the tray</span>")
    if capture_running:
        return ('<span class="capchip"><span class="led" style="background:#1F6F5C;'
                'box-shadow:0 0 0 3px rgba(31,111,92,.22)"></span>capturing'
                '<form method="post" action="/api/capture/stop"><button class="capbtn">stop</button></form></span>')
    return ('<span class="capchip"><span class="led" style="background:#B26A1B"></span>stopped'
            '<form method="post" action="/api/capture/start"><button class="capbtn">start</button></form></span>')


def _masthead(date_str: str, total: int, n_tasks: int, capture_running) -> str:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    big = f"{dt.day} {dt.strftime('%B')}"    # platform-independent (no %-d / %#d)
    sub = dt.strftime("%A, %Y")
    return (
        '<div class="eyebrowrow"><p class="eyebrow">Daily review</p>'
        f'{_capchip(capture_running)}</div>'
        '<div class="masthead">'
        f'<h1 class="date">{big}<span>{sub}</span></h1>'
        f'<div class="stats"><div class="big">{human_duration(total)}</div>'
        f'<div class="sub">focused &middot; {n_tasks} task{"s" if n_tasks != 1 else ""}</div></div>'
        "</div>"
    )


def _time_bars(stats: dict) -> str:
    total = stats["total_active"] or 1
    rows = []
    top_app = stats["by_app"][0][0] if stats["by_app"] else None
    for app, secs in stats["by_app"][:6]:
        pct = secs / total * 100
        accent = " accent" if app == top_app else ""
        rows.append(
            f'<div class="barrow{accent}"><span class="name">{html.escape(app)}</span>'
            f'<span class="track"><span class="fill" style="width:{pct:.0f}%"></span></span>'
            f'<span class="val">{human_duration(secs)}</span></div>'
        )
    return '<div class="bars">' + "".join(rows) + "</div>"


def _render_entry(e: dict) -> str:
    cls = "entry" + (" struggle" if e["struggled"] else "") + (" learned" if e["learned"] else "")
    tags = ""
    chips = []
    if e["struggled"]:
        chips.append('<span class="tag struggle">struggled</span>')
    if e["learned"]:
        chips.append('<span class="tag learned">learned</span>')
    if e["status"] == "proposed":
        chips.append('<span class="tag muted">draft</span>')
    if chips:
        tags = '<div class="tags">' + "".join(chips) + "</div>"
    note = f'<p class="note">{html.escape(e["note"] or e["summary"])}</p>' if (e["note"] or e["summary"]) else ""
    steps = ""
    if e["steps"]:
        items = "".join(f"<li>{html.escape(s)}</li>" for s in e["steps"])
        steps = f'<ul class="steps">{items}</ul>'
    if e["host"]:
        steps += f'<div style="margin-top:8px"><span class="host">{html.escape(e["host"])}</span></div>'
    cmds = "".join(f'<div class="cmd">{html.escape(c)}</div>' for c in e["commands"])
    return (
        f'<article class="{cls}"><div class="entry__time">{e["start"]}</div>'
        '<div class="entry__body">'
        f'<div class="head"><h2 class="title">{html.escape(e["name"])}</h2>'
        f'<span class="dur">{human_duration(e["secs"])}</span></div>'
        f"{tags}{note}{steps}{cmds}</div></article>"
    )


def render_day(stats: dict, flash: str = "", flash_err: bool = False,
               capture_running=None, edit_mode: bool = False,
               nav: tuple[str, str] | None = None) -> str:
    date_str = stats["date"]
    total = stats["total_active"]
    prev, nxt = nav or (None, None)
    page = lambda body: _page(f"daylog · {date_str}", date_str, body, prev, nxt)
    flash_html = (f'<div class="flash{" err" if flash_err else ""}">{html.escape(flash)}</div>'
                  if flash else "")
    d = html.escape(date_str, quote=True)

    # Nothing captured at all.
    if not stats["segments"] and not stats["shots"] and not stats["tasks"]:
        return page(
            flash_html + _masthead(date_str, total, 0, capture_running)
            + '<div class="empty">Nothing captured for this day.<br>'
            'Capture runs in the background while the tray app is open.<br><br>'
            '<a href="/days" style="font-family:var(--mono);font-size:14px;font-style:normal;'
            'border-bottom:1px solid var(--line)">Browse the days that do have data &rarr;</a></div>'
        )

    masthead = _masthead(date_str, total, len(stats["tasks"]), capture_running)

    # Edit mode: the task editor.
    if edit_mode:
        if not stats["tasks"]:
            forms = '<div class="empty" style="padding:40px">No tasks to edit yet.</div>'
        else:
            blocks = []
            for t in stats["tasks"]:
                name = html.escape(t["name"] or "", quote=True)
                summary = html.escape(t["summary"] or "")
                blocks.append(
                    f'<form method="post" action="/api/task" class="tedit">'
                    f'<input type="hidden" name="id" value="{t["id"]}">'
                    f'<input type="hidden" name="date" value="{d}">'
                    f'<input type="hidden" name="edit" value="1">'
                    '<div class="head">'
                    f'<input class="tname" name="name" value="{name}">'
                    f'<span class="dur">{human_duration(t["total_secs"])}</span></div>'
                    '<div class="tags" style="margin-top:10px">'
                    f'<label class="chk"><input type="checkbox" name="struggled" '
                    f'{"checked" if t["struggled"] else ""}> struggled</label>'
                    f'<label class="chk"><input type="checkbox" name="learned" '
                    f'{"checked" if t["learned"] else ""}> learned</label>'
                    '<button class="btn ghost" style="margin-left:auto;padding:6px 14px">Save</button>'
                    "</div>"
                    f'<textarea name="summary" placeholder="What were you doing?">{summary}</textarea>'
                    "</form>"
                )
            forms = "".join(blocks)
        return page(flash_html + masthead
                    + '<p class="section-label">Edit tasks</p>' + forms
                    + '<div class="actions"><div class="subtle">'
                    f'<a href="/day/{d}">Done editing</a></div></div>')

    # Not summarized yet.
    if not stats["entries"]:
        bars = _section("Where the time went", _time_bars(stats)) if stats["by_app"] else ""
        body = (
            flash_html + masthead
            + '<p class="lede" style="font-style:normal;color:var(--muted)">'
            "This day isn't summarized yet. Build the story from what was captured.</p>"
            + bars
            + '<div class="actions">'
            f'<form id="publishform" method="post" action="/api/label">'
            f'<input type="hidden" name="date" value="{d}">'
            '<button class="publish" type="submit">Summarize this day <kbd>&crarr;</kbd></button></form>'
            "</div>"
            + _frames(stats) + _foot()
        )
        return page(body)

    # Full journal.
    lede = f'<p class="lede">{stats["lede"]}</p>' if stats["lede"] else ""
    log = '<section class="log">' + "".join(_render_entry(e) for e in stats["entries"]) + "</section>"
    bars = _section("Where the time went", _time_bars(stats)) if stats["by_app"] else ""
    actions = (
        '<div class="actions">'
        f'<form id="publishform" method="post" action="/api/publish">'
        f'<input type="hidden" name="date" value="{d}">'
        '<button class="publish" type="submit">Publish this day <kbd>&crarr;</kbd></button></form>'
        '<div class="subtle">'
        f'<a href="/day/{d}?edit=1">Edit the tasks</a>'
        f'<a href="/note/{d}">Read the note</a>'
        f'<form method="post" action="/api/label" style="display:inline">'
        f'<input type="hidden" name="date" value="{d}">'
        '<button type="submit">Re-summarize</button></form>'
        "</div></div>"
    )
    return page(flash_html + masthead + lede + log + bars + actions + _frames(stats) + _foot())


def _section(label: str, inner: str) -> str:
    return f'<p class="section-label">{html.escape(label)}</p>{inner}'


def _frames(stats: dict) -> str:
    if not stats["shots"]:
        return ""
    cards = []
    for sh in stats["shots"][:18]:
        badge = '<span class="badge">manual</span>' if sh["source"] == "manual" else ""
        meta = html.escape((sh["app"] or "") + " · " + sh["ts"][11:16])
        cards.append(
            f'<div class="frame">{badge}'
            f'<a href="/img/{sh["id"]}?full=1" target="_blank"><img loading="lazy" src="/img/{sh["id"]}"></a>'
            f'<div class="meta" title="{html.escape(sh["title"] or "")}">{meta}</div></div>'
        )
    return _section(f"Frames ({len(stats['shots'])})", '<div class="frames">' + "".join(cards) + "</div>")


def _foot() -> str:
    return ('<p class="foot"><kbd>&crarr;</kbd> publish &nbsp; <kbd>/</kbd> search &nbsp; '
            '<kbd>&larr;</kbd><kbd>&rarr;</kbd> change day</p>')


def render_search(rows, query: str) -> str:
    if not rows:
        body = f'<div class="empty">No screenshots match &ldquo;{html.escape(query)}&rdquo;.</div>'
        return _page("daylog · search", local_date_str(), body)
    cards = []
    for r in rows:
        badge = '<span class="badge">manual</span>' if r["source"] == "manual" else ""
        snippet = ""
        if r["ocr_text"] and query.lower() in r["ocr_text"].lower():
            idx = r["ocr_text"].lower().index(query.lower())
            snippet = html.escape(r["ocr_text"][max(0, idx - 40): idx + 60])
        cards.append(
            f'<div class="frame">{badge}'
            f'<a href="/img/{r["id"]}?full=1" target="_blank"><img loading="lazy" src="/img/{r["id"]}"></a>'
            f'<div class="meta">{html.escape((r["app"] or "") + " · " + r["ts"][:16])}</div>'
            f'<div class="meta">{snippet}</div></div>'
        )
    body = (f'<p class="section-label">{len(rows)} match(es) for &ldquo;{html.escape(query)}&rdquo;</p>'
            f'<div class="frames">{"".join(cards)}</div>')
    return _page("daylog · search", local_date_str(), body)


def render_days(days: list[dict], data_dir, flash: str = "", flash_err: bool = False) -> str:
    """The archive: every day that has captured data, newest first, grouped by month."""
    today = local_date_str()
    flash_html = (f'<div class="flash{" err" if flash_err else ""}">{html.escape(flash)}</div>'
                  if flash else "")
    if not days:
        body = (flash_html + '<div class="empty">No days captured yet in<br>'
                f'<code>{html.escape(str(data_dir))}</code>.<br><br>'
                'If your old data lives somewhere else, point daylog at it in '
                '<a href="/settings" style="border-bottom:1px solid var(--line)">settings</a>.</div>')
        return _page("daylog · archive", today, body)

    total_active = sum(d["active"] for d in days)
    total_tasks = sum(d["tasks"] for d in days)
    out, month = [], None
    for d in days:
        dt = datetime.strptime(d["date"], "%Y-%m-%d")
        m = dt.strftime("%B %Y")
        if m != month:
            if month is not None:
                out.append("</div>")
            month = m
            out.append(f'<p class="month">{m}</p><div class="daylist">')
        if d["tasks"]:
            state, cls = f'{d["tasks"]} task{"s" if d["tasks"] != 1 else ""}', ""
        else:
            state, cls = "not summarized", " raw"
        if d["published"]:
            state, cls = "published", " pub"
        bits = []
        if d["shots"]:
            bits.append(f'{d["shots"]} frame{"s" if d["shots"] != 1 else ""}')
        if d["segments"]:
            bits.append(f'{d["segments"]} segments')
        headline = (f'{d["tasks"]} task{"s" if d["tasks"] != 1 else ""} recorded'
                    if d["tasks"] else "Captured, not yet summarized")
        out.append(
            f'<a class="dayrow{" is-today" if d["date"] == today else ""}" href="/day/{d["date"]}">'
            f'<span class="num">{dt.day}<small>{dt.strftime("%a")}</small></span>'
            f'<span class="what"><b>{headline}</b>'
            f'<div class="sub">{html.escape(" · ".join(bits) or "no frames")}</div></span>'
            f'<span class="right"><div class="time">{human_duration(d["active"])}</div>'
            f'<div class="state{cls}">{state}</div></span></a>'
        )
    out.append("</div>")
    body = (
        flash_html
        + '<div class="eyebrowrow"><p class="eyebrow">Archive</p></div>'
        '<h1 class="date" style="font-size:34px">All captured days</h1>'
        f'<p class="lede" style="font-size:19px;margin:18px 0 8px">'
        f'{len(days)} day{"s" if len(days) != 1 else ""} on record — '
        f'{human_duration(total_active)} of focused time and {total_tasks} '
        f'task{"s" if total_tasks != 1 else ""}, from '
        f'{days[-1]["date"]} to {days[0]["date"]}.</p>'
        f'<p class="foot" style="text-align:left;margin:0 0 10px">'
        f'reading <code style="font-family:var(--mono)">{html.escape(str(data_dir))}</code></p>'
        + "".join(out)
    )
    return _page("daylog · archive", today, body)


def other_data_dirs(current) -> list[tuple[str, int]]:
    """Likely daylog data folders other than the one in use, as (path, days_of_data).

    Lets you find an older store (an earlier install wrote to ~/.daylog; a packaged build may
    have started fresh) without hunting for it on disk.
    """
    from .config import DEFAULT_DATA_DIR, PROJECT_ROOT

    candidates = [DEFAULT_DATA_DIR, PROJECT_ROOT / "data", Path("D:/daylog-data")]
    seen, out = {Path(current).resolve()}, []
    for c in candidates:
        try:
            rc = Path(c).resolve()
        except OSError:
            continue
        if rc in seen or not (rc / "daylog.db").exists():
            continue
        seen.add(rc)
        try:
            conn = db.connect(rc / "daylog.db")
            n = conn.execute(
                "SELECT COUNT(DISTINCT date(ts,'localtime')) FROM screenshots WHERE deleted = 0"
            ).fetchone()[0]
            conn.close()
        except sqlite3.Error:
            n = 0
        out.append((str(rc), n))
    return out


def render_note(conn: sqlite3.Connection, date_str: str) -> str:
    """The published markdown for a day, read back from the DB.

    daylog writes the note as plain markdown to disk; this shows the same text in the app so
    Obsidian is somewhere to *keep* notes, not somewhere you must go to read them.
    """
    row = conn.execute(
        "SELECT markdown, vault_path, published_at FROM daily_reports WHERE date = ?", (date_str,)
    ).fetchone()
    if not row or not row["markdown"]:
        body = ('<div class="empty">No note published for this day yet.<br>'
                f'<a href="/day/{date_str}" style="font-family:var(--mono);font-size:14px;'
                'font-style:normal;border-bottom:1px solid var(--line)">'
                'Summarize and publish it &rarr;</a></div>')
        return _page(f"daylog · note · {date_str}", date_str, body)
    where = row["vault_path"] or ""
    body = (
        '<div class="eyebrowrow"><p class="eyebrow">Published note</p></div>'
        f'<h1 class="date" style="font-size:34px">{html.escape(date_str)}</h1>'
        f'<p class="foot" style="text-align:left;margin:10px 0 18px">'
        f'written to <code style="font-family:var(--mono)">{html.escape(where)}</code></p>'
        f'<div class="panel"><pre class="note-md">{html.escape(row["markdown"])}</pre></div>'
        f'<div class="actions"><div class="subtle"><a href="/day/{date_str}">Back to the day</a>'
        '</div></div>'
    )
    return _page(f"daylog · note · {date_str}", date_str, body)


def render_settings(cfg, status: dict, autostart_on: bool,
                    flash: str = "", flash_err: bool = False) -> str:
    e = lambda s: html.escape(str(s or ""), quote=True)
    providers = ["lmstudio", "ollama", "claude", "openai"]
    opts = "".join(f'<option value="{p}"{" selected" if cfg.ai.provider == p else ""}>{p}</option>'
                   for p in providers)
    flash_html = (f'<div class="flash{" err" if flash_err else ""}">{html.escape(flash)}</div>'
                  if flash else "")

    def statline(label, ok, detail):
        col = "#1F6F5C" if ok else "#B26A1B"
        return (f'<div class="statline"><span class="led" style="background:{col}"></span>'
                f'{html.escape(label)}<span class="det">{html.escape(detail)}</span></div>')

    status_html = (statline("Activity watcher", status["watcher"], status["watcher_detail"])
                   + statline("Screenshot text (OCR)", status["ocr"], status["ocr_detail"])
                   + statline(f"AI · {cfg.ai.provider}", status["ai"], status["ai_detail"])
                   + statline("Notes folder", status["vault"], status["vault_detail"]))

    autostart_btn = (
        '<form method="post" action="/api/autostart">'
        f'<input type="hidden" name="action" value="{"disable" if autostart_on else "enable"}">'
        f'<button class="btn{" ghost" if autostart_on else ""}">'
        f'{"Disable launch on login" if autostart_on else "Launch on login"}</button></form>'
    )

    others = other_data_dirs(cfg.storage.data_dir)
    if others:
        chips = "".join(
            '<form method="post" action="/api/data_dir" style="margin:0 8px 8px 0">'
            f'<input type="hidden" name="data_dir" value="{e(p)}">'
            '<button class="btn ghost" style="padding:6px 12px;font-size:13px;font-weight:400">'
            f'Load {e(p)} '
            f'<span style="color:var(--faint)">({n} day{"s" if n != 1 else ""})</span>'
            "</button></form>"
            for p, n in others
        )
        found = ('<p style="color:var(--muted);font-size:13px;margin:14px 0 8px">'
                 "Other daylog data found on this machine — one click switches to it:</p>"
                 f'<div style="display:flex;flex-wrap:wrap">{chips}</div>')
    else:
        found = ""

    body = f"""
    {flash_html}
    <div class="eyebrowrow"><p class="eyebrow">Settings</p></div>
    <h1 class="date" style="font-size:34px;margin-bottom:24px">Connections</h1>

    <div class="panel"><h2>Status</h2>{status_html}
      <div style="margin-top:14px">{autostart_btn}</div>
      <p style="color:var(--faint);font-size:13px;margin:10px 0 0">
        Autostart is <b>{"on" if autostart_on else "off"}</b> — when on, daylog opens in the tray at login.</p>
    </div>

    <div class="panel"><h2>Data folder</h2>
      <form method="post" action="/api/data_dir">
        <div class="field"><label>Where the timeline database and screenshots live</label>
          <input name="data_dir" value="{e(cfg.storage.data_dir)}"></div>
        <button class="btn" type="submit">Load this folder</button>
      </form>
      <p style="color:var(--faint);font-size:13px;margin:10px 0 0">
        Point daylog at an existing folder to read its history — the app reloads it immediately and
        new captures are written there too. Nothing is moved or deleted.</p>
      {found}
    </div>

    <form method="post" action="/api/settings">
      <div class="panel"><h2>AI summaries</h2>
        <div class="field"><label>Provider</label><select name="provider">{opts}</select></div>
        <div class="twocol">
          <div class="field"><label>LM Studio URL</label>
            <input name="lmstudio_base_url" value="{e(cfg.ai.lmstudio_base_url)}"></div>
          <div class="field"><label>LM Studio model (blank = loaded one)</label>
            <input name="lmstudio_model" value="{e(cfg.ai.lmstudio_model)}"></div>
          <div class="field"><label>Ollama model</label>
            <input name="ollama_model" value="{e(cfg.ai.ollama_model)}"></div>
          <div class="field"><label>Claude model</label>
            <input name="claude_model" value="{e(cfg.ai.claude_model)}"></div>
        </div>
      </div>

      <div class="panel"><h2>Notes</h2>
        <div class="field"><label>Obsidian vault (optional — leave empty to keep notes inside
          the daylog data folder)</label>
          <input name="vault_path" value="{e(cfg.obsidian.vault_path)}"></div>
        <label class="field row"><input type="checkbox" name="auto_detect"
          {"checked" if cfg.obsidian.auto_detect else ""}> Find the vault automatically if the path moves</label>
        <label class="field row"><input type="checkbox" name="embed_all_screenshots"
          {"checked" if cfg.obsidian.embed_all_screenshots else ""}> Embed all screenshots in notes</label>
      </div>

      <div class="panel"><h2>Capture</h2>
        <div class="twocol">
          <div class="field"><label>Screenshot hotkey (restart to apply)</label>
            <input name="hotkey" value="{e(cfg.capture.hotkey)}"></div>
          <div class="field"><label>Keep full screenshots for (days)</label>
            <input name="retention_days" value="{cfg.storage.screenshot_retention_days}"></div>
        </div>
      </div>

      <button class="btn" type="submit">Save settings</button>
    </form>
    """
    return _page("daylog · settings", local_date_str(), body)


# ------------------------------------------------------------------------------ server


class DBHandle:
    """The open timeline DB, swappable at runtime when the data folder changes."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def switch(self, db_path: Path) -> None:
        old = self.conn
        self.conn = db.init_db(db_path)   # bind the new one first; only then drop the old
        try:
            old.close()
        except sqlite3.Error:
            pass


def make_handler(conn: sqlite3.Connection, cfg=None, controller=None):
    if cfg is None:
        cfg = get_config()
    dbh = conn if isinstance(conn, DBHandle) else DBHandle(conn)

    def capture_running():
        return controller.running if controller is not None else None

    def gather_status() -> dict:
        from . import llm, ocr, watcher

        w = watcher.status(cfg)
        ai_ready, ai_reason = llm.availability(cfg)
        vp = cfg.obsidian.vault_path
        return {
            "watcher": w["ok"],
            "watcher_detail": (f"{w['app'] or 'idle'} · "
                               f"{'browser URLs on' if w['urls'] else 'no URL tracking'}"),
            "ai": ai_ready,
            # Without a provider the built-in labeler takes over, so this is not an error.
            "ai_detail": "ready" if ai_ready else f"{ai_reason} — using the built-in labeler",
            "ocr": ocr.available(),
            "ocr_detail": (f"{ocr.backend_name()} engine" if ocr.available()
                           else "no engine — screenshot text is not searchable"),
            "vault": bool(vp and vp.exists()),
            "vault_detail": str(vp) if vp else "not set",
        }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _send(self, body: str, status=200, ctype="text/html; charset=utf-8"):
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _redirect(self, location):
            self.send_response(302)
            self.send_header("Location", location)
            self.end_headers()

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path
            qs = parse_qs(parsed.query)
            if path == "/":
                return self._redirect(f"/day/{local_date_str()}")
            if path.startswith("/day/"):
                date_str = path[len("/day/"):]
                try:
                    datetime.strptime(date_str, "%Y-%m-%d")
                except ValueError:
                    return self._send("bad date", 400, "text/plain")
                return self._send(render_day(
                    day_stats(dbh.conn, date_str), qs.get("flash", [""])[0],
                    qs.get("err", ["0"])[0] == "1", capture_running(),
                    qs.get("edit", ["0"])[0] == "1",
                    neighbor_days(dbh.conn, date_str),
                ))
            if path.startswith("/note/"):
                date_str = path[len("/note/"):]
                try:
                    datetime.strptime(date_str, "%Y-%m-%d")
                except ValueError:
                    return self._send("bad date", 400, "text/plain")
                return self._send(render_note(dbh.conn, date_str))
            if path == "/days":
                return self._send(render_days(
                    days_index(dbh.conn), cfg.storage.data_dir,
                    qs.get("flash", [""])[0], qs.get("err", ["0"])[0] == "1",
                ))
            if path == "/jump":
                d = qs.get("d", [""])[0]
                try:
                    datetime.strptime(d, "%Y-%m-%d")
                except ValueError:
                    return self._redirect("/days")
                return self._redirect(f"/day/{d}")
            if path == "/settings":
                from . import autostart
                return self._send(render_settings(
                    cfg, gather_status(), autostart.status()["enabled"],
                    qs.get("flash", [""])[0], qs.get("err", ["0"])[0] == "1",
                ))
            if path == "/search":
                q = qs.get("q", [""])[0]
                return self._send(render_search(search_ocr(dbh.conn, q), q))
            if path.startswith("/img/"):
                return self._serve_image(path, parsed.query)
            return self._send("not found", 404, "text/plain")

        def do_POST(self):
            parsed = urlparse(self.path)
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8") if length else ""
            form = {k: v[0] for k, v in parse_qs(body).items()}
            date_str = form.get("date", local_date_str())
            edit = form.get("edit") == "1"

            def back(flash="", err=False):
                loc = f"/day/{date_str}"
                extra = []
                if edit:
                    extra.append("edit=1")
                if flash:
                    extra.append(f"flash={quote(flash)}")
                    if err:
                        extra.append("err=1")
                if extra:
                    loc += "?" + "&".join(extra)
                return self._redirect(loc)

            if parsed.path == "/api/task":
                dbh.conn.execute(
                    "UPDATE tasks SET name=?, summary=?, struggled=?, learned=?, status='confirmed' "
                    "WHERE id=?",
                    (form.get("name", ""), form.get("summary", ""),
                     1 if "struggled" in form else 0, 1 if "learned" in form else 0,
                     int(form.get("id", "0"))),
                )
                dbh.conn.commit()
                return back("Saved.")

            if parsed.path == "/api/label":
                from . import llm
                from .label import label_day
                from .segment import segment_day
                try:
                    segment_day(dbh.conn, cfg, date_str)
                    tasks = label_day(dbh.conn, cfg, date_str)
                    return back(f"Summarized into {len(tasks)} task(s).")
                except llm.LLMError as exc:
                    return back(f"AI summary failed: {exc}", err=True)

            if parsed.path == "/api/publish":
                from .obsidian import PublishError, publish_day
                try:
                    result = publish_day(dbh.conn, cfg, date_str)
                    return back(f"Published {result['tasks']} task(s) to Obsidian.")
                except PublishError as exc:
                    return back(f"Publish failed: {exc}", err=True)

            if parsed.path == "/api/data_dir":
                from .config import save_config

                raw = form.get("data_dir", "").strip()
                if not raw:
                    return self._redirect(
                        f"/settings?err=1&flash={quote('Give a folder path first.')}")
                new_dir = Path(raw).expanduser()
                if new_dir.resolve() == Path(cfg.storage.data_dir).resolve():
                    return self._redirect(
                        f"/settings?flash={quote('Already reading that folder.')}")
                try:
                    new_dir.mkdir(parents=True, exist_ok=True)
                    (new_dir / "screenshots").mkdir(exist_ok=True)
                    dbh.switch(new_dir / "daylog.db")
                except (OSError, sqlite3.Error) as exc:
                    return self._redirect(
                        f"/settings?err=1&flash={quote(f'Could not open {new_dir}: {exc}')}")

                # Persist, and point capture at the new folder too (it opens the DB on start).
                cfg.storage.data_dir = new_dir
                save_config(cfg)
                if controller is not None and controller.running:
                    controller.stop()
                    controller.start()
                n = len(days_index(dbh.conn))
                return self._redirect(
                    f"/days?flash={quote(f'Loaded {new_dir} — {n} day(s) of history.')}")

            if parsed.path == "/api/capture/start":
                if controller:
                    controller.start()
                return back("Capture started.")

            if parsed.path == "/api/capture/stop":
                if controller:
                    controller.stop()
                return back("Capture stopped.")

            if parsed.path == "/api/autostart":
                from . import autostart
                if form.get("action") == "enable":
                    autostart.enable()
                    msg = "Launch on login enabled."
                else:
                    autostart.disable()
                    msg = "Launch on login disabled."
                return self._redirect(f"/settings?flash={quote(msg)}")

            if parsed.path == "/api/settings":
                cfg.ai.provider = form.get("provider", cfg.ai.provider)
                cfg.ai.lmstudio_base_url = form.get("lmstudio_base_url", cfg.ai.lmstudio_base_url)
                cfg.ai.lmstudio_model = form.get("lmstudio_model", "")
                cfg.ai.ollama_model = form.get("ollama_model", cfg.ai.ollama_model)
                cfg.ai.claude_model = form.get("claude_model", cfg.ai.claude_model)
                vp = form.get("vault_path", "").strip()
                cfg.obsidian.vault_path = Path(vp) if vp else None
                cfg.obsidian.auto_detect = "auto_detect" in form
                cfg.obsidian.embed_all_screenshots = "embed_all_screenshots" in form
                cfg.capture.hotkey = form.get("hotkey", cfg.capture.hotkey) or cfg.capture.hotkey
                try:
                    cfg.storage.screenshot_retention_days = int(form.get("retention_days", "14"))
                except ValueError:
                    pass
                from .config import save_config
                save_config(cfg)
                return self._redirect(f"/settings?flash={quote('Settings saved.')}")

            return self._send("not found", 404, "text/plain")

        def _serve_image(self, path, query):
            try:
                shot_id = int(path[len("/img/"):])
            except ValueError:
                return self._send("bad id", 400, "text/plain")
            row = dbh.conn.execute(
                "SELECT path, thumb_path FROM screenshots WHERE id = ?", (shot_id,)
            ).fetchone()
            if not row:
                return self._send("not found", 404, "text/plain")
            full = parse_qs(query).get("full", ["0"])[0] == "1"
            fpath = row["path"] if full else (row["thumb_path"] or row["path"])
            data = None
            try:
                with open(fpath, "rb") as fh:
                    data = fh.read()
            except OSError:
                if full and row["thumb_path"]:
                    try:
                        with open(row["thumb_path"], "rb") as fh:
                            data = fh.read()
                    except OSError:
                        data = None
            if data is None:
                return self._send("image missing", 404, "text/plain")
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    cfg = get_config()
    conn = db.init_db(cfg.db_path)
    server = ThreadingHTTPServer((host, port), make_handler(conn, cfg))
    print(f"daylog dashboard: http://{host}:{port}/  (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


def serve_background(host: str = "127.0.0.1", port: int = 8765, controller=None):
    """Start the dashboard in a daemon thread (for the tray). Returns (server, url)."""
    import threading

    cfg = get_config()
    conn = db.init_db(cfg.db_path)
    server = ThreadingHTTPServer((host, port), make_handler(conn, cfg, controller))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://{host}:{port}/"
