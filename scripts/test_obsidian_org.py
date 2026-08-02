"""Validate the organized Obsidian output: date folders, no duplicates, legacy cleanup."""

import json
import tempfile
from pathlib import Path

from daylog import db
from daylog.config import Config, ObsidianConfig, StorageConfig
from daylog.obsidian import publish_day

tmp = Path(tempfile.mkdtemp())
vault = tmp / "vault"; vault.mkdir()
cfg = Config(storage=StorageConfig(data_dir=tmp),
             obsidian=ObsidianConfig(vault_path=vault, auto_detect=False))
conn = db.init_db(cfg.db_path)
date = "2026-06-23"


def add_task(name, secs=600):
    steps = json.dumps({"ai_steps": ["did a thing"], "timed": [
        {"label": "valuelyne", "app": "Code.exe", "host": None, "secs": secs,
         "ts_start": f"{date}T10:00:00+00:00", "ts_end": f"{date}T10:10:00+00:00"}], "note": ""})
    conn.execute("INSERT INTO tasks(date,name,summary,total_secs,struggled,learned,status,steps,"
                 "screenshot_ids) VALUES(?,?,?,?,0,0,'confirmed',?,'[]')",
                 (date, name, "summary", secs, steps))
    conn.commit()


add_task("Set up Cloudflare tunnel")
add_task("Review PRs")

# --- date-wise folder structure ---
res = publish_day(conn, cfg, date)
day_dir = vault / "Activity" / "2026-06" / "2026-06-23"
assert day_dir.is_dir(), "expected Activity/2026-06/2026-06-23/ folder"
assert (day_dir / "2026-06-23.md").exists(), "daily note in the day folder"
assert (day_dir / "attachments").is_dir()
notes = sorted(p.name for p in day_dir.glob("*.md"))
assert f"{date} · Set up Cloudflare tunnel.md" in notes, notes
assert f"{date} · Review PRs.md" in notes, notes
assert len(notes) == 3, notes  # daily + 2 task notes
# Nothing dumped flat in Activity/ root.
flat = [p.name for p in (vault / "Activity").glob("*.md")]
assert flat == [], f"no flat files expected, found {flat}"
print("OK: organized into Activity/<month>/<date>/ with daily + task notes")

# --- no duplicates on re-publish, even when a task is renamed ---
conn.execute("UPDATE tasks SET name='Wire up the Cloudflare tunnel' WHERE name=?",
             ("Set up Cloudflare tunnel",))
conn.commit()
publish_day(conn, cfg, date)
notes2 = sorted(p.name for p in day_dir.glob("*.md"))
assert f"{date} · Set up Cloudflare tunnel.md" not in notes2, "old renamed note should be gone"
assert f"{date} · Wire up the Cloudflare tunnel.md" in notes2, notes2
assert len(notes2) == 3, f"still 3 notes, no duplicates: {notes2}"
print("OK: re-publish replaces (rename leaves no duplicate)")

# --- legacy flat-format cleanup ---
root = vault / "Activity"
(root / f"{date}.md").write_text("old flat daily", encoding="utf-8")
(root / f"{date} - Old Task.md").write_text("old flat task", encoding="utf-8")
old_attach = root / "attachments"; old_attach.mkdir(exist_ok=True)
(old_attach / "daylog-1.jpg").write_bytes(b"x")
# task references screenshot id 1 so legacy cleanup targets it
conn.execute("UPDATE tasks SET screenshot_ids='[1]' WHERE name='Review PRs'")
conn.commit()
publish_day(conn, cfg, date)
assert not (root / f"{date}.md").exists(), "legacy flat daily should be removed"
assert not (root / f"{date} - Old Task.md").exists(), "legacy flat task should be removed"
assert not (old_attach / "daylog-1.jpg").exists(), "legacy embed should be removed"
print("OK: legacy flat-format files cleaned up on publish")

print("\nALL OBSIDIAN ORGANIZATION CHECKS PASSED")
