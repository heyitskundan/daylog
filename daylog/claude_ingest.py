"""Read Claude Code session transcripts as the primary 'what did I do' signal.

Claude Code writes one JSONL transcript per session under ~/.claude/projects/<proj>/<id>.jsonl.
Each line is an event. The useful ones:
  - {"type":"ai-title", "aiTitle": "..."}            -> a ready-made task title
  - {"type":"user", "message":{"content":"..."}, "origin":{"kind":"human"}}  -> your prompt (intent)
  - {"type":"assistant", "message":{"content":[{"type":"tool_use","name":...,"input":...}]}}  -> actions
  - {"type":"user", "message":{"content":[{"type":"tool_result","is_error":true}]}}  -> an error (struggle signal)

This turns "Code.exe for 40m" into "Fix the null error in the portfolio editor — 6 edits, 2 errors",
grounded in what you actually asked Claude to do.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, time, timezone
from pathlib import Path

from .config import Config


def _logs_dir(cfg: Config) -> Path:
    return cfg.claude_code.logs_dir or (Path.home() / ".claude" / "projects")


def _in_range(ts: str | None, start_iso: str, end_iso: str) -> bool:
    return bool(ts) and start_iso <= ts < end_iso


def parse_session(path: Path, start_iso: str, end_iso: str) -> dict | None:
    """Parse one transcript, keeping only activity within [start_iso, end_iso)."""
    title = cwd = None
    prompts: list[str] = []
    tools: Counter = Counter()
    files: set[str] = set()
    bash: list[str] = []
    edits = writes = errors = 0
    ts_min = ts_max = None

    def stamp(ts):
        nonlocal ts_min, ts_max
        if ts:
            ts_min = ts if ts_min is None or ts < ts_min else ts_min
            ts_max = ts if ts_max is None or ts > ts_max else ts_max

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = obj.get("type")
                if t == "ai-title":
                    title = obj.get("aiTitle") or title
                    continue
                cwd = cwd or obj.get("cwd")
                ts = obj.get("timestamp")
                if not _in_range(ts, start_iso, end_iso):
                    continue

                if t == "user":
                    content = (obj.get("message") or {}).get("content")
                    if isinstance(content, str):
                        if (obj.get("origin") or {}).get("kind") == "human" \
                                and content.strip() and not content.lstrip().startswith("/"):
                            prompts.append(content.strip())
                            stamp(ts)
                    elif isinstance(content, list):
                        for b in content:
                            if isinstance(b, dict) and b.get("type") == "tool_result" \
                                    and b.get("is_error"):
                                errors += 1
                                stamp(ts)
                elif t == "assistant":
                    for b in (obj.get("message") or {}).get("content", []):
                        if isinstance(b, dict) and b.get("type") == "tool_use":
                            name = b.get("name") or "?"
                            inp = b.get("input") or {}
                            tools[name] += 1
                            stamp(ts)
                            if name in ("Edit", "MultiEdit"):
                                edits += 1
                            elif name == "Write":
                                writes += 1
                            elif name == "Bash":
                                cmd = inp.get("command")
                                if cmd:
                                    bash.append(cmd)
                            fp = inp.get("file_path")
                            if fp:
                                files.add(Path(str(fp)).name)
    except OSError:
        return None

    if ts_min is None and not prompts:
        return None  # nothing happened in this day
    return {
        "session_id": path.stem,
        "title": title,
        "project": Path(cwd).name if cwd else None,
        "cwd": cwd,
        "start": ts_min,
        "end": ts_max,
        "prompts": prompts,
        "edits": edits,
        "writes": writes,
        "bash": bash,
        "files": sorted(files),
        "errors": errors,
        "tools": dict(tools),
    }


def sessions_for_day(cfg: Config, date_str: str) -> list[dict]:
    """All Claude Code sessions with activity on the given local day, sorted by start time."""
    if not cfg.claude_code.enabled:
        return []
    base = _logs_dir(cfg)
    if not base.exists():
        return []

    day = datetime.strptime(date_str, "%Y-%m-%d").date()
    start_local = datetime.combine(day, time.min).astimezone()
    end_local = datetime.combine(day, time.max).astimezone()
    start_iso = start_local.astimezone(timezone.utc).isoformat()
    end_iso = end_local.astimezone(timezone.utc).isoformat()
    # A session touching this day was last written on/after the day started (minus a buffer).
    mtime_cutoff = start_local.timestamp() - 86400

    out = []
    for path in base.rglob("*.jsonl"):
        try:
            if path.stat().st_mtime < mtime_cutoff:
                continue
        except OSError:
            continue
        s = parse_session(path, start_iso, end_iso)
        if s and (s["prompts"] or s["edits"] or s["writes"] or s["bash"]):
            out.append(s)
    out.sort(key=lambda s: s["start"] or "")
    return out


def render_context(sessions: list[dict], max_prompts: int = 4) -> str:
    """Format Claude Code sessions for the labeler prompt."""
    if not sessions:
        return ""
    lines = [
        "CLAUDE CODE SESSIONS (what the editor/terminal time was ACTUALLY about). Name tasks "
        "from the user's ASKS and the actions/files below, NOT from the auto-title (it is often "
        "inaccurate or merges unrelated topics). One session may be several tasks:",
    ]
    for s in sessions:
        when = f"{(s['start'] or '')[11:16]}-{(s['end'] or '')[11:16]}"
        lines.append(
            f"- [{when}] project {s['project'] or '?'} | edits {s['edits']}, "
            f"writes {s['writes']}, bash {len(s['bash'])}, errors {s['errors']}"
        )
        for p in s["prompts"][:max_prompts]:
            one = " ".join(p.split())
            lines.append(f"    asked: {one[:160]}")
        if s["files"]:
            lines.append("    files touched: " + ", ".join(s["files"][:8]))
        if s["title"]:
            lines.append(f"    (auto-title, may be wrong: {s['title']})")
    return "\n".join(lines)
