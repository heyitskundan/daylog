"""Deterministic segmenter.

Turns the raw window/web/afk timeline into coarse "segments" - contiguous blocks of work on
the same thing. A new segment starts when the app changes, the window title changes enough,
the browser host changes, or there is an idle gap. No LLM is involved; this is the factual
backbone the (opt-in) AI labeler later groups into named tasks.

Each segment also carries heuristic struggle/learn `signals` for the labeler to reason over.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from urllib.parse import urlparse

from .config import Config
from .util import iso, parse_iso

# Hosts that suggest looking something up / problem-solving rather than producing.
RESEARCH_HOSTS = (
    "google.", "bing.", "duckduckgo.", "stackoverflow.com", "stackexchange.com",
    "github.com", "developer.mozilla.org", "docs.", "chatgpt.com", "claude.ai",
    "reddit.com", "youtube.com",
)
LONG_DWELL_SECS = 25 * 60
HIGH_CHURN_PER_MIN = 4.0

# The built-in watcher writes 'window'/'web'/'afk'. Rows captured before it existed came from
# ActivityWatch and are tagged 'aw-*'; both are read so old history keeps working.
WINDOW_SOURCES = ("window", "aw-window")
WEB_SOURCES = ("web", "aw-web")
AFK_SOURCES = ("afk", "aw-afk")


@dataclass
class Segment:
    ts_start: str
    ts_end: str
    primary_app: str | None = None
    project_guess: str | None = None
    url_host: str | None = None
    n_switches: int = 0
    idle_secs: int = 0
    active_secs: int = 0
    signals: dict = field(default_factory=dict)


def _local_day_bounds_utc(date_str: str) -> tuple[str, str]:
    """Return (start_utc_iso, end_utc_iso) for a local calendar date YYYY-MM-DD."""
    day = datetime.strptime(date_str, "%Y-%m-%d").date()
    start_local = datetime.combine(day, time.min).astimezone()
    end_local = datetime.combine(day, time.max).astimezone()
    return iso(start_local.astimezone(timezone.utc)), iso(end_local.astimezone(timezone.utc))


def _tokens(title: str | None) -> set[str]:
    if not title:
        return set()
    return {t for t in "".join(c.lower() if c.isalnum() else " " for c in title).split() if len(t) > 2}


def _title_distance(a: str | None, b: str | None) -> float:
    """Jaccard distance between two titles' token sets (0 = identical, 1 = disjoint)."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta and not tb:
        return 0.0
    if not ta or not tb:
        return 1.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return 1.0 - inter / union


def _host(url: str | None) -> str | None:
    if not url:
        return None
    try:
        netloc = urlparse(url).netloc.lower()
        return netloc[4:] if netloc.startswith("www.") else netloc or None
    except Exception:
        return None


def _is_research_host(host: str | None) -> bool:
    return bool(host) and any(h.rstrip(".") in host for h in (r.rstrip(".") for r in RESEARCH_HOSTS))


def _project_from_title(title: str | None) -> str | None:
    """Heuristic project name from an editor-style title like 'file.py - myproject - VSCode'."""
    if not title:
        return None
    parts = [p.strip() for p in title.split(" - ") if p.strip()]
    # Editors usually put the workspace/project in the middle segment.
    if len(parts) >= 3:
        return parts[-2]
    return None


def _dominant_host(conn, seg_start: str, seg_end: str) -> str | None:
    """Most frequent browser host among web events that fall within this segment."""
    # Half-open [start, end) so an event at a segment boundary belongs to one segment only.
    rows = conn.execute(
        f"SELECT url FROM events WHERE source IN {WEB_SOURCES} AND ts_start >= ? AND ts_start < ?",
        (seg_start, seg_end),
    ).fetchall()
    counts: dict[str, int] = {}
    for r in rows:
        h = _host(r["url"])
        if h:
            counts[h] = counts.get(h, 0) + 1
    if not counts:
        return None
    return max(counts, key=counts.get)


def _afk_intervals(conn, start_utc, end_utc) -> list[tuple[datetime, datetime]]:
    """'afk' (away) periods overlapping the day, as datetime ranges."""
    rows = conn.execute(
        f"SELECT ts_start, ts_end, extra FROM events WHERE source IN {AFK_SOURCES} "
        "AND ts_start BETWEEN ? AND ? ORDER BY ts_start",
        (start_utc, end_utc),
    ).fetchall()
    out = []
    for r in rows:
        try:
            status = json.loads(r["extra"] or "{}").get("status")
        except (ValueError, TypeError):
            status = None
        if status == "afk" and r["ts_end"]:
            out.append((parse_iso(r["ts_start"]), parse_iso(r["ts_end"])))
    return out


def _overlap_secs(s: datetime, e: datetime, intervals: list[tuple[datetime, datetime]]) -> float:
    total = 0.0
    for a, b in intervals:
        lo, hi = max(s, a), min(e, b)
        if hi > lo:
            total += (hi - lo).total_seconds()
    return total


def _failed_cmds(conn, start_utc, end_utc) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM terminal_cmds "
        "WHERE ts BETWEEN ? AND ? AND exit_code IS NOT NULL AND exit_code != 0",
        (start_utc, end_utc),
    ).fetchone()
    return row["c"] if row else 0


def segment_day(conn: sqlite3.Connection, cfg: Config, date_str: str) -> list[Segment]:
    """Rebuild segments for a local calendar day. Returns the segments (also persisted)."""
    start_utc, end_utc = _local_day_bounds_utc(date_str)
    win = conn.execute(
        f"SELECT ts_start, ts_end, app, title, extra FROM events "
        f"WHERE source IN {WINDOW_SOURCES} AND ts_start BETWEEN ? AND ? ORDER BY ts_start",
        (start_utc, end_utc),
    ).fetchall()

    idle_gap = cfg.segment.idle_gap_seconds
    afk = _afk_intervals(conn, start_utc, end_utc)

    segments: list[Segment] = []
    cur: Segment | None = None
    prev_end: datetime | None = None
    cur_title: str | None = None

    for ev in win:
        ev_start = parse_iso(ev["ts_start"])
        ev_end = parse_iso(ev["ts_end"]) if ev["ts_end"] else ev_start
        raw = max(0, (ev_end - ev_start).total_seconds())
        # Subtract any time the user was away (AFK) — a window stays "focused" while idle.
        idle = _overlap_secs(ev_start, ev_end, afk)
        dur = max(0, raw - idle)
        app = ev["app"]
        title = ev["title"]

        gap = (ev_start - prev_end).total_seconds() if prev_end else 0.0
        new_block = (
            cur is None
            or app != cur.primary_app
            or _title_distance(title, cur_title) > cfg.segment.title_change_ratio
            or gap > idle_gap
        )

        if new_block:
            if cur is not None:
                segments.append(cur)
            cur = Segment(
                ts_start=ev["ts_start"],
                ts_end=ev["ts_end"] or ev["ts_start"],
                primary_app=app,
                project_guess=_project_from_title(title),
            )
        else:
            assert cur is not None
            cur.ts_end = ev["ts_end"] or ev["ts_start"]
            cur.n_switches += 1
            if gap > 0:
                cur.idle_secs += int(gap)

        cur.active_secs += int(dur)
        cur.idle_secs += int(idle)
        cur_title = title
        prev_end = ev_end

    if cur is not None:
        segments.append(cur)

    # Backstop: active time can never exceed the segment's wall-clock span.
    for seg in segments:
        span = (parse_iso(seg.ts_end) - parse_iso(seg.ts_start)).total_seconds()
        seg.active_secs = max(0, min(seg.active_secs, int(span)))

    # Resolve each segment's dominant browser host and struggle/learn signals.
    for seg in segments:
        seg.url_host = _dominant_host(conn, seg.ts_start, seg.ts_end)
        minutes = max(seg.active_secs / 60.0, 0.0001)
        churn = seg.n_switches / minutes
        research = _is_research_host(seg.url_host)
        failed = _failed_cmds(conn, seg.ts_start, seg.ts_end)
        long_dwell = seg.active_secs >= LONG_DWELL_SECS
        high_churn = churn >= HIGH_CHURN_PER_MIN
        seg.signals = {
            "long_dwell": long_dwell,
            "high_churn": high_churn,
            "research_host": research,
            "failed_cmds": failed,
            # Struggle prior: casual browsing is NOT struggle. Real signals are failed
            # commands, churn, and a long fight that also involves heavy searching.
            "struggle_score": round(
                min(failed, 3) * 0.7
                + (0.7 if high_churn else 0.0)
                + (0.6 if (long_dwell and research) else 0.0),
                2,
            ),
        }

    _persist(conn, start_utc, end_utc, segments)
    return segments


def _persist(conn, start_utc, end_utc, segments: list[Segment]) -> None:
    with conn:
        conn.execute(
            "DELETE FROM segments WHERE ts_start BETWEEN ? AND ?", (start_utc, end_utc)
        )
        for s in segments:
            conn.execute(
                "INSERT INTO segments(ts_start, ts_end, primary_app, project_guess, url_host, "
                "n_switches, idle_secs, active_secs, signals) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    s.ts_start, s.ts_end, s.primary_app, s.project_guess, s.url_host,
                    s.n_switches, s.idle_secs, s.active_secs, json.dumps(s.signals),
                ),
            )
