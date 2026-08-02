"""Opt-in AI labeler.

Groups the day's deterministic segments into named tasks with sub-steps and struggle/learn
flags. The LLM only *names and groups* — every duration is computed from the segments, so the
numbers stay deterministic (per the deterministic-default preference). Run via `daylog label`;
it never runs automatically.
"""

from __future__ import annotations

import json
import re
import sqlite3

from . import db, llm
from .config import Config
from .segment import WINDOW_SOURCES, _local_day_bounds_utc
from .util import human_duration

TASK_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "summary": {"type": "string"},
                    "struggled": {"type": "boolean"},
                    "learned": {"type": "boolean"},
                    "note": {"type": "string"},
                    "steps": {"type": "array", "items": {"type": "string"}},
                    "segment_indexes": {"type": "array", "items": {"type": "integer"}},
                },
                "required": [
                    "name", "summary", "struggled", "learned", "note",
                    "steps", "segment_indexes",
                ],
            },
        }
    },
    "required": ["tasks"],
}

SYSTEM = (
    "You turn a developer's noisy timeline into a short, honest, USEFUL record of what they "
    "accomplished and learned, written so they can document it later. Input: Claude Code "
    "sessions (the real work), plus deterministic 'segments' (blocks on one app/window) with "
    "signals, terminal commands, and on-screen text.\n\n"
    "Rules:\n"
    "- A task is something the person MADE HAPPEN: built, fixed, set up, configured, deployed, "
    "or learned by doing. Reviewing, reading, exploring, navigating, or glancing at files/pages "
    "is NOT a task on its own; fold it into the task it served, or drop it. Also drop true noise "
    "(quick searches, speed tests, checking notifications).\n"
    "- KEEP every genuine accomplishment or LEARNING as its own task, even if brief or outside "
    "Claude Code (e.g. setting up a tool, configuring an app, fixing a bug). Never merge a "
    "distinct accomplishment into an unrelated task, and never drop it as noise. Aim for a "
    "handful of real tasks, not one per file or page.\n"
    "- Name each task by what was done, the way the person would say it ('Set up the Obsidian "
    "vault'), never by app ('Used Chrome', 'Worked in VS Code').\n"
    "- summary: one plain sentence on what they did or achieved.\n"
    "- steps: the concrete, ORDERED steps they took, specific enough that they could repeat the "
    "task from these notes. This is the documentation — for setup/learning tasks especially, "
    "capture the real how-to steps (use the asks, commands, files, and on-screen text as "
    "evidence). Do not narrate every window switch; capture the steps that reproduce the work.\n"
    "- learned: true ONLY when they created, set up, fixed, or figured out something new and "
    "concrete (a working config, a deploy, a bug fix, a new workflow). Reading docs, reviewing, "
    "or exploring code is NOT 'learned'. When true, the steps should read like a how-to.\n"
    "- struggled: true only with real evidence (repeated attempts, failed commands, errors, a "
    "long fight with one problem).\n"
    "- note: one short line of WHY, only when struggled or learned is true.\n"
    "- CLAUDE CODE SESSIONS: name each task from the user's actual ASKS and the actions/files, "
    "NOT from the session 'auto-title' (often inaccurate or merges unrelated topics). Split a "
    "session into separate tasks if it covered separate goals. Errors/repeated asks = struggle.\n"
    "- Use segment 'signals' as hints, not gospel. Never invent activity not in the data. "
    "Write plainly. Never use em dashes."
)


# --- output sanitisation: weak local models leak field names, wrap quotes, and sometimes
#     degenerate into runaway number/word sequences. Clean their output before it's stored. ---

_LABEL_PREFIX = re.compile(r"^\s*(name|summary|note|step|task|title)\s*[:\-]\s*", re.IGNORECASE)
# 4+ run-on numbers (any separators incl. newlines/brackets) => degenerate counting
_RUNAWAY = re.compile(r"(\d+[\s,;.)\]]+){4,}\d*")
_REPEAT = re.compile(r"(\b\w+\b)(\s+\1){3,}", re.IGNORECASE)  # same word 4+ times in a row


def _clean_text(s, maxlen: int = 260) -> str:
    s = str(s or "").strip()
    s = _LABEL_PREFIX.sub("", s)
    s = s.strip().strip("\"'“”‘’").strip()
    s = re.sub(r"\s+", " ", s)
    # Salvage the readable head by cutting at a runaway number/word sequence.
    for pat in (_RUNAWAY, _REPEAT):
        m = pat.search(s)
        if m:
            s = s[:m.start()].rstrip(" ,;:-")
    if len(s) > maxlen:
        s = s[:maxlen].rstrip() + "…"
    return s


def _is_degenerate(s: str) -> bool:
    if not s:
        return True
    if _RUNAWAY.search(s) or _REPEAT.search(s):
        return True
    digits = sum(c.isdigit() for c in s)
    return len(s) > 40 and digits / len(s) > 0.35


def _clean_name(s) -> str:
    return _clean_text(s, 90) or "Untitled task"


def _clean_steps(steps) -> list[str]:
    out, seen = [], set()
    for raw in (steps or [])[:12]:
        c = _clean_text(raw, 160)
        key = c.lower()
        if c and not _is_degenerate(c) and key not in seen:
            seen.add(key)
            out.append(c)
        if len(out) >= 8:
            break
    return out


def _significant(segments, limit: int = 80, min_secs: int = 20) -> list:
    """Drop trivial blocks and cap the count, so the prompt stays small on busy days."""
    segs = [s for s in segments if s["active_secs"] >= min_secs] or list(segments)
    if len(segs) > limit:
        segs = sorted(segs, key=lambda s: -s["active_secs"])[:limit]
    return sorted(segs, key=lambda s: s["ts_start"])


def _build_context(conn: sqlite3.Connection, segments, start_utc, end_utc) -> str:
    lines = ["SEGMENTS (index | time | active | app | project | host | signals | sample titles):"]
    for i, s in enumerate(segments):
        titles = conn.execute(
            f"SELECT DISTINCT title FROM events WHERE source IN {WINDOW_SOURCES} "
            "AND ts_start >= ? AND ts_start < ? AND title IS NOT NULL LIMIT 4",
            (s["ts_start"], s["ts_end"]),
        ).fetchall()
        title_str = " | ".join(t["title"][:70] for t in titles if t["title"])
        sig = json.loads(s["signals"] or "{}")
        sig_str = ",".join(
            k for k in ("long_dwell", "high_churn", "research_host") if sig.get(k)
        ) or "none"
        if sig.get("failed_cmds"):
            sig_str += f",failed_cmds={sig['failed_cmds']}"
        lines.append(
            f"[{i}] {s['ts_start'][11:16]}-{s['ts_end'][11:16]} "
            f"{human_duration(s['active_secs'])} | {s['primary_app'] or '?'} | "
            f"{s['project_guess'] or '-'} | {s['url_host'] or '-'} | {sig_str} | {title_str}"
        )

    cmds = conn.execute(
        "SELECT ts, command FROM terminal_cmds WHERE ts >= ? AND ts < ? ORDER BY ts LIMIT 80",
        (start_utc, end_utc),
    ).fetchall()
    if cmds:
        lines.append("\nTERMINAL COMMANDS:")
        lines += [f"  {c['ts'][11:16]} {c['command'][:120]}" for c in cmds]

    ocr = conn.execute(
        "SELECT ts, ocr_text FROM screenshots WHERE ts >= ? AND ts < ? AND deleted=0 "
        "AND ocr_text IS NOT NULL AND length(ocr_text) > 0 ORDER BY ts LIMIT 12",
        (start_utc, end_utc),
    ).fetchall()
    if ocr:
        lines.append("\nON-SCREEN TEXT SNIPPETS:")
        lines += [f"  {o['ts'][11:16]} {o['ocr_text'][:160].strip()}" for o in ocr]

    return "\n".join(lines)


def _titles_for(conn, segs, limit: int = 5) -> list[str]:
    """Distinct window titles seen during these segments, most-used first."""
    counts: dict[str, int] = {}
    for s in segs:
        rows = conn.execute(
            f"SELECT title FROM events WHERE source IN {WINDOW_SOURCES} "
            "AND ts_start >= ? AND ts_start < ? AND title IS NOT NULL",
            (s["ts_start"], s["ts_end"]),
        ).fetchall()
        for r in rows:
            t = (r["title"] or "").strip()
            if t:
                counts[t] = counts.get(t, 0) + 1
    ordered = sorted(counts, key=lambda t: -counts[t])
    return ordered[:limit]


def _pretty_app(app: str | None) -> str:
    if not app:
        return "something"
    name = app[:-4] if app.lower().endswith(".exe") else app
    return {"Code": "VS Code", "chrome": "Chrome", "msedge": "Edge",
            "WindowsTerminal": "the terminal"}.get(name, name)


def propose_tasks_builtin(conn: sqlite3.Connection, segments) -> list[dict]:
    """Group the day's segments into tasks without an LLM.

    Deterministic and always available: blocks are grouped by what they were *on* — the
    project folder from the window title, else the browser host, else the app — and named
    from that. Durations already come from the segments, so this produces the same numbers
    the LLM path would; only the prose is plainer. struggled/learned reuse the signals the
    segmenter already computed rather than inventing a judgement.
    """
    groups: dict[str, list] = {}
    for i, s in enumerate(segments):
        key = (s["project_guess"] or s["url_host"] or s["primary_app"] or "other").lower()
        groups.setdefault(key, []).append((i, s))

    tasks = []
    for items in groups.values():
        idxs = [i for i, _ in items]
        segs = [s for _, s in items]
        total = sum(s["active_secs"] for s in segs)
        if total < 60 and len(groups) > 1:
            continue                      # a stray minute is noise, not a task

        sig = [json.loads(s["signals"] or "{}") for s in segs]
        struggled = any(x.get("struggle_score", 0) >= 1.0 for x in sig)
        learned = any(x.get("research_host") for x in sig) and total >= 600

        first = segs[0]
        if first["project_guess"]:
            name = f"Worked on {first['project_guess']}"
        elif first["url_host"]:
            name = f"Researched on {first['url_host']}"
        else:
            name = f"Spent time in {_pretty_app(first['primary_app'])}"

        apps = sorted({s["primary_app"] for s in segs if s["primary_app"]})
        summary = (f"{human_duration(total)} across {len(segs)} block(s) "
                   f"in {', '.join(_pretty_app(a) for a in apps[:3])}.")
        tasks.append({
            "name": name,
            "summary": summary,
            "struggled": struggled,
            "learned": learned,
            "note": "",
            "steps": _titles_for(conn, segs),
            "segment_indexes": idxs,
        })

    tasks.sort(key=lambda t: -sum(segments[i]["active_secs"] for i in t["segment_indexes"]))
    return tasks


def label_day(conn: sqlite3.Connection, cfg: Config, date_str: str,
              use_ai: bool | None = None) -> list[dict]:
    """Turn a day's segments into named tasks. Returns the proposed tasks (also persisted).

    Uses the configured LLM (local or cloud) when one is reachable, and the built-in
    deterministic labeler when none is — so summarizing always works, with or without AI.
    If a provider IS configured but fails, the error surfaces rather than silently
    downgrading: you asked for that model, so you should hear that it broke.
    """
    start_utc, end_utc = _local_day_bounds_utc(date_str)
    segments = conn.execute(
        "SELECT * FROM segments WHERE ts_start >= ? AND ts_start < ? ORDER BY ts_start",
        (start_utc, end_utc),
    ).fetchall()
    if not segments:
        return []
    # Keep the prompt (and the model's job) manageable on busy days: feed the significant
    # blocks only. The same reduced list backs the index mapping below, so indexes stay valid.
    segments = _significant(segments)

    ready, _reason = llm.availability(cfg)
    if use_ai is None:
        use_ai = ready

    if use_ai:
        context = _build_context(conn, segments, start_utc, end_utc)

        from . import claude_ingest
        cc_block = claude_ingest.render_context(claude_ingest.sessions_for_day(cfg, date_str))
        if cc_block:
            context = cc_block + "\n\n" + context

        user = (
            f"Date: {date_str}\n\n{context}\n\n"
            "Much of the window/web activity is noise. Return only the few real tasks that matter. "
            "Lead with the Claude Code sessions (that's the real work); map the relevant segments "
            "into each task by their exact indexes, and omit noise segments."
        )
        tasks = llm.complete_json(cfg, SYSTEM, user, TASK_SCHEMA).get("tasks", [])
    else:
        tasks = propose_tasks_builtin(conn, segments)

    persisted = []
    with db.transaction(conn):
        conn.execute(
            "DELETE FROM tasks WHERE date = ? AND status = 'proposed'", (date_str,)
        )
        for t in tasks:
            idxs = [i for i in t.get("segment_indexes", []) if 0 <= i < len(segments)]
            segs = [segments[i] for i in idxs]
            total = sum(s["active_secs"] for s in segs)
            timed = [
                {
                    "label": (s["project_guess"] or s["primary_app"] or "work"),
                    "app": s["primary_app"],
                    "host": s["url_host"],
                    "secs": s["active_secs"],
                    "ts_start": s["ts_start"],
                    "ts_end": s["ts_end"],
                }
                for s in segs
            ]
            ts_start = min((s["ts_start"] for s in segs), default=date_str)
            ts_end = max((s["ts_end"] for s in segs), default=date_str)

            name = _clean_name(t.get("name"))
            summary = _clean_text(t.get("summary", ""), 300)
            note = "" if _is_degenerate(t.get("note", "")) else _clean_text(t.get("note", ""), 300)
            steps = _clean_steps(t.get("steps"))

            steps_json = json.dumps({"ai_steps": steps, "timed": timed, "note": note})
            shot_ids = _screenshots_in_range(conn, ts_start, ts_end)
            cur = conn.execute(
                "INSERT INTO tasks(date, name, summary, total_secs, struggled, learned, "
                "status, steps, screenshot_ids) VALUES(?,?,?,?,?,?, 'proposed', ?, ?)",
                (
                    date_str, name, summary,
                    total, int(bool(t.get("struggled"))), int(bool(t.get("learned"))),
                    steps_json, json.dumps(shot_ids),
                ),
            )
            persisted.append(
                {
                    "id": cur.lastrowid, "name": name, "total_secs": total,
                    "struggled": t.get("struggled"), "learned": t.get("learned"),
                    "note": note,
                }
            )
    return persisted


def _screenshots_in_range(conn, ts_start, ts_end) -> list[int]:
    rows = conn.execute(
        "SELECT id FROM screenshots WHERE ts >= ? AND ts <= ? AND deleted = 0 ORDER BY ts",
        (ts_start, ts_end),
    ).fetchall()
    return [r["id"] for r in rows]
