"""Obsidian publisher — the "document properly" output.

Each day gets its own folder so the vault stays organized:

    <vault>/Activity/<YYYY-MM>/<YYYY-MM-DD>/
        <YYYY-MM-DD>.md          (the daily note, links to the task notes)
        <YYYY-MM-DD> · <task>.md  (one note per task)
        attachments/             (downscaled screenshots embedded in the notes)

Publishing is a full replace: the day's folder is cleared and rewritten from the database
(the source of truth), so re-publishing never leaves duplicates. Legacy flat-format files from
older builds are cleaned up for the day being published.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
from pathlib import Path

from .config import Config
from .segment import _local_day_bounds_utc
from .util import human_duration, iso, utcnow


class PublishError(RuntimeError):
    pass


def _slug(text: str) -> str:
    text = re.sub(r'[\\/:*?"<>|#^\[\]]', "", text)   # strip filename + wikilink-illegal chars
    return re.sub(r"\s+", " ", text).strip()[:80] or "task"


def notes_root(cfg: Config) -> Path:
    """Where notes are written: the Obsidian vault if there is one, else daylog's own folder.

    Obsidian is an optional *destination*, never a requirement — the output is plain markdown
    either way. With no vault configured (or a vault that has moved away), the day is written
    under <data_dir>/notes, which always exists and is readable in the dashboard.
    """
    vp = cfg.obsidian.vault_path
    if vp and Path(vp).exists():
        return Path(vp)
    root = cfg.storage.data_dir / "notes"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _day_dir(cfg: Config, date_str: str) -> Path:
    # Activity / 2026-06 / 2026-06-23
    return notes_root(cfg) / cfg.obsidian.notes_subdir / date_str[:7] / date_str


def _time_by_app(conn, start_utc, end_utc) -> list[tuple[str, int]]:
    rows = conn.execute(
        "SELECT primary_app AS app, SUM(active_secs) AS secs FROM segments "
        "WHERE ts_start >= ? AND ts_start < ? GROUP BY primary_app ORDER BY secs DESC",
        (start_utc, end_utc),
    ).fetchall()
    return [(r["app"] or "?", r["secs"]) for r in rows]


def _embed_screenshot(cfg, attach_dir: Path, attach_rel: str, conn, shot_id: int) -> str | None:
    """Copy a downscaled screenshot into the day's attachments. Returns its vault-relative path."""
    row = conn.execute(
        "SELECT path FROM screenshots WHERE id = ? AND deleted = 0", (shot_id,)
    ).fetchone()
    if not row:
        return None
    src = Path(row["path"])
    if not src.exists():
        return None
    out_name = f"shot-{shot_id}.jpg"
    try:
        from PIL import Image

        img = Image.open(src)
        if img.width > cfg.obsidian.embed_max_width:
            r = cfg.obsidian.embed_max_width / img.width
            img = img.resize((cfg.obsidian.embed_max_width, int(img.height * r)))
        img.convert("RGB").save(attach_dir / out_name, "JPEG", quality=72)
    except Exception:
        return None
    return f"{attach_rel}/{out_name}"


def _task_note(cfg, conn, day_dir: Path, attach_dir: Path, attach_rel: str,
               date_str: str, task) -> str:
    """Write one task note into the day folder. Returns its wiki title (filename without .md)."""
    steps = json.loads(task["steps"] or "{}")
    title = f"{date_str} · {_slug(task['name'])}"
    flags = []
    if task["struggled"]:
        flags.append("struggled")
    if task["learned"]:
        flags.append("learned")

    lines = [
        "---",
        f"date: {date_str}",
        f"time_spent: {human_duration(task['total_secs'])}",
        f"tags: [daylog, {', '.join(flags) if flags else 'task'}]",
        "---",
        "",
        f"# {task['name']}",
        "",
        task["summary"] or "",
        "",
    ]

    note = steps.get("note")
    if note and (task["struggled"] or task["learned"]):
        label = "Struggled" if task["struggled"] else "Learned"
        lines += [f"> [!note] {label}", f"> {note}", ""]

    ai_steps = steps.get("ai_steps") or []
    if ai_steps:
        lines.append("## Steps")
        lines += [f"{i}. {s}" for i, s in enumerate(ai_steps, 1)]
        lines.append("")

    timed = steps.get("timed") or []
    if timed:
        lines.append("## Time breakdown")
        lines.append("| When | What | Time |")
        lines.append("|---|---|---|")
        for ts in timed:
            what = ts.get("label") or ts.get("app") or "work"
            if ts.get("host"):
                what += f" ({ts['host']})"
            lines.append(f"| {ts['ts_start'][11:16]} | {what} | {human_duration(ts['secs'])} |")
        lines.append("")

    cmds = conn.execute(
        "SELECT command FROM terminal_cmds WHERE ts >= ? AND ts <= ? ORDER BY ts",
        (_min_ts(timed, date_str), _max_ts(timed, date_str)),
    ).fetchall()
    if cmds:
        lines.append("## Commands")
        lines.append("```")
        lines += [c["command"] for c in cmds]
        lines.append("```")
        lines.append("")

    shot_ids = json.loads(task["screenshot_ids"] or "[]")
    if shot_ids:
        if not cfg.obsidian.embed_all_screenshots:
            shot_ids = _pick_key_shots(conn, shot_ids)
        embeds = []
        for sid in shot_ids:
            rel = _embed_screenshot(cfg, attach_dir, attach_rel, conn, sid)
            if rel:
                embeds.append((rel, _is_manual(conn, sid)))
        if embeds:
            lines.append("## Screenshots")
            for rel, manual in embeds:
                if manual:
                    lines.append("**Manual capture**")
                lines.append(f"![[{rel}]]")
                lines.append("")

    (day_dir / f"{title}.md").write_text("\n".join(lines), encoding="utf-8")
    return title


def _min_ts(timed, date_str):
    return min((t["ts_start"] for t in timed), default=date_str)


def _max_ts(timed, date_str):
    return max((t.get("ts_end") or t["ts_start"] for t in timed), default=date_str)


def _is_manual(conn, shot_id) -> bool:
    row = conn.execute("SELECT source FROM screenshots WHERE id = ?", (shot_id,)).fetchone()
    return bool(row and row["source"] == "manual")


def _pick_key_shots(conn, shot_ids: list[int]) -> list[int]:
    manual, first_auto = [], None
    for sid in shot_ids:
        row = conn.execute("SELECT source FROM screenshots WHERE id = ?", (sid,)).fetchone()
        if row and row["source"] == "manual":
            manual.append(sid)
        elif first_auto is None:
            first_auto = sid
    return manual + ([first_auto] if first_auto else [])


def _clean_legacy(cfg: Config, date_str: str, shot_ids: list[int]) -> None:
    """Remove old flat-format files for this date (from builds before date folders)."""
    root = notes_root(cfg) / cfg.obsidian.notes_subdir
    if not root.exists():
        return
    # Old daily + task notes lived directly under Activity/ as "<date>*.md".
    for p in root.glob(f"{date_str}*.md"):
        if p.is_file():
            p.unlink(missing_ok=True)
    # Old embeds lived in a single Activity/attachments/ as daylog-<id>.jpg.
    old_attach = root / "attachments"
    if old_attach.is_dir():
        for sid in shot_ids:
            (old_attach / f"daylog-{sid}.jpg").unlink(missing_ok=True)
        # Drop the folder if it's now empty.
        try:
            next(old_attach.iterdir())
        except StopIteration:
            old_attach.rmdir()


def publish_day(conn: sqlite3.Connection, cfg: Config, date_str: str) -> dict:
    vault = notes_root(cfg)      # the vault if configured, else <data_dir>/notes

    tasks = conn.execute(
        "SELECT * FROM tasks WHERE date = ? ORDER BY total_secs DESC", (date_str,)
    ).fetchall()
    if not tasks:
        raise PublishError(f"No tasks for {date_str}. Summarize the day first.")

    all_shot_ids = []
    for t in tasks:
        all_shot_ids += json.loads(t["screenshot_ids"] or "[]")

    # Full replace: clear the day's folder, then rewrite it from the DB. No duplicates.
    day_dir = _day_dir(cfg, date_str)
    if day_dir.exists():
        shutil.rmtree(day_dir)
    attach_dir = day_dir / "attachments"
    attach_dir.mkdir(parents=True, exist_ok=True)
    attach_rel = f"{cfg.obsidian.notes_subdir}/{date_str[:7]}/{date_str}/attachments"
    _clean_legacy(cfg, date_str, all_shot_ids)

    start_utc, end_utc = _local_day_bounds_utc(date_str)
    total = sum(t["total_secs"] for t in tasks)

    task_links = [(_task_note(cfg, conn, day_dir, attach_dir, attach_rel, date_str, t), t)
                  for t in tasks]

    lines = [
        "---",
        f"date: {date_str}",
        f"total_active: {human_duration(total)}",
        f"task_count: {len(tasks)}",
        "tags: [daylog, daily]",
        "---",
        "",
        f"# {date_str}",
        "",
        f"**Total active time:** {human_duration(total)}  |  **Tasks:** {len(tasks)}",
        "",
        "## Tasks",
    ]
    for title, t in task_links:
        marks = []
        if t["struggled"]:
            marks.append("struggled")
        if t["learned"]:
            marks.append("learned")
        suffix = f"  ({', '.join(marks)})" if marks else ""
        lines.append(f"- [[{title}|{t['name']}]] · {human_duration(t['total_secs'])}{suffix}")
    lines.append("")

    lines.append("## Time by app")
    lines.append("| App | Time |")
    lines.append("|---|---|")
    for app, secs in _time_by_app(conn, start_utc, end_utc)[:12]:
        lines.append(f"| {app} | {human_duration(secs)} |")
    lines.append("")

    struggles = [t for _, t in task_links if t["struggled"]]
    learned = [t for _, t in task_links if t["learned"]]
    if struggles:
        lines.append("## Where I struggled")
        for t in struggles:
            note = json.loads(t["steps"] or "{}").get("note", "")
            lines.append(f"- **{t['name']}**: {note}")
        lines.append("")
    if learned:
        lines.append("## What I learned")
        for t in learned:
            note = json.loads(t["steps"] or "{}").get("note", "")
            lines.append(f"- **{t['name']}**: {note}")
        lines.append("")

    daily_path = day_dir / f"{date_str}.md"
    daily_path.write_text("\n".join(lines), encoding="utf-8")

    conn.execute(
        "INSERT INTO daily_reports(date, markdown, generated_by, vault_path, published_at) "
        "VALUES(?,?,?,?,?) ON CONFLICT(date) DO UPDATE SET "
        "markdown=excluded.markdown, vault_path=excluded.vault_path, published_at=excluded.published_at",
        (date_str, "\n".join(lines), cfg.ai.provider, str(daily_path), iso(utcnow())),
    )
    conn.commit()

    return {"daily_note": str(daily_path), "tasks": len(tasks), "vault": str(vault),
            "folder": str(day_dir)}
