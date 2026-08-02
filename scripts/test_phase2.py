"""Validate Phase 2: terminal tailer, AI labeler (fake LLM), and Obsidian publisher.

No network and no Pillow required — the LLM is monkeypatched and screenshot embedding
degrades gracefully when image files/PIL are absent.
"""

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from daylog import db, llm
from daylog.config import (ClaudeCodeConfig, Config, ObsidianConfig, StorageConfig,
                           TerminalConfig)
from daylog.segment import segment_day
from daylog.util import iso, local_date_str

tmp = Path(tempfile.mkdtemp())
vault = tmp / "vault"
vault.mkdir()
cfg = Config(
    storage=StorageConfig(data_dir=tmp),
    obsidian=ObsidianConfig(vault_path=vault, embed_all_screenshots=True),
    claude_code=ClaudeCodeConfig(enabled=False),   # isolate: don't scan real ~/.claude logs
)
conn = db.init_db(cfg.db_path)

base = datetime.now().astimezone().replace(hour=10, minute=0, second=0, microsecond=0)
date_str = local_date_str(base)


def add_window(off_min, dur, app, title):
    s = base + timedelta(minutes=off_min)
    e = s + timedelta(seconds=dur)
    conn.execute(
        "INSERT INTO events(ts_start, ts_end, source, app, title, extra) VALUES(?,?,?,?,?,?)",
        (iso(s.astimezone(timezone.utc)), iso(e.astimezone(timezone.utc)),
         "aw-window", app, title, json.dumps({"duration": dur})),
    )


# Two work blocks: Cloudflare setup in browser+terminal, then coding.
for i in range(12):
    add_window(i, 60, "chrome.exe", "Cloudflare Zero Trust dashboard")
for i in range(15):
    add_window(12 + i, 60, "Code.exe", "tunnel.py - valuelyne - Visual Studio Code")
conn.commit()

# ---- terminal tailer ----
hist = tmp / "ConsoleHost_history.txt"
hist.write_text("old-command-1\nold-command-2\n", encoding="utf-8")
tcfg = Config(storage=StorageConfig(data_dir=tmp),
              terminal=TerminalConfig(enabled=True, powershell_history=hist))
from daylog import terminal

assert terminal.tail(conn, tcfg) == 0, "first sight should baseline, not import history"
with open(hist, "a", encoding="utf-8") as fh:
    fh.write("cloudflared tunnel login\ncloudflared tunnel create valuelyne\n")
n = terminal.tail(conn, tcfg)
assert n == 2, f"expected 2 new commands, got {n}"
assert terminal.tail(conn, tcfg) == 0, "no new commands on re-tail"
cmds = conn.execute("SELECT command FROM terminal_cmds ORDER BY id").fetchall()
assert any("tunnel create" in c["command"] for c in cmds), [c["command"] for c in cmds]
print("OK: terminal tailer (baseline, capture, idempotent)")

# A command timestamped inside the Cloudflare window (the live tailer stamps "now"; here we
# place one in-window to exercise the publisher's command-folding deterministically).
conn.execute(
    "INSERT INTO terminal_cmds(ts, shell, command) VALUES(?,?,?)",
    (iso((base + timedelta(minutes=5)).astimezone(timezone.utc)),
     "powershell", "cloudflared tunnel create valuelyne"),
)
conn.commit()

# ---- segmenter ----
segs = segment_day(conn, cfg, date_str)
assert len(segs) >= 2, len(segs)

# ---- AI labeler with a FAKE LLM ----
def fake_complete_json(_cfg, _system, _user, _schema):
    # Group segment 0 (chrome) and 1 (code) into two tasks.
    return {
        "tasks": [
            {
                "name": "Set up Cloudflare tunnel",
                "summary": "Configured a Cloudflare Zero Trust tunnel for valuelyne.",
                "struggled": True, "learned": True,
                "note": "Took a while to get the tunnel token + DNS right.",
                "steps": ["Open Cloudflare dashboard", "Create tunnel", "Run cloudflared"],
                "segment_indexes": [0],
            },
            {
                "name": "Wire tunnel into the app",
                "summary": "Edited tunnel.py to use the new tunnel.",
                "struggled": False, "learned": False, "note": "",
                "steps": ["Edit tunnel.py"],
                "segment_indexes": [1],
            },
        ]
    }


llm.availability = lambda _cfg: (True, "fake")
llm.complete_json = fake_complete_json

from daylog.label import label_day

tasks = label_day(conn, cfg, date_str)
assert len(tasks) == 2, tasks
cf = [t for t in tasks if "Cloudflare" in t["name"]][0]
assert cf["struggled"] and cf["total_secs"] > 0, cf
rows = conn.execute("SELECT name, status, total_secs FROM tasks WHERE date=?", (date_str,)).fetchall()
assert all(r["status"] == "proposed" for r in rows), rows
print("OK: AI labeler (grouping, deterministic time, struggle flag, persisted)")

# ---- Obsidian publisher ----
from daylog.obsidian import publish_day

result = publish_day(conn, cfg, date_str)
daily = Path(result["daily_note"])
assert daily.exists(), daily
text = daily.read_text(encoding="utf-8")
assert "Total active time" in text
assert "Set up Cloudflare tunnel" in text
assert "[[" in text and "]]" in text            # wikilinks to task notes
assert "Where I struggled" in text

# Task note exists in the day folder and is well-formed.
day_folder = vault / "Activity" / date_str[:7] / date_str
task_note = day_folder / f"{date_str} · Set up Cloudflare tunnel.md"
assert task_note.exists(), list(day_folder.glob("*.md"))
tn = task_note.read_text(encoding="utf-8")
assert "# Set up Cloudflare tunnel" in tn
assert "Time breakdown" in tn
assert "cloudflared tunnel create valuelyne" in tn   # commands folded into the task note
assert "—" not in tn and "–" not in tn               # no em/en dashes (voice rule)

# Idempotent re-publish.
publish_day(conn, cfg, date_str)
assert daily.exists()
print("OK: Obsidian publisher (daily note, task notes, links, commands, no em dashes, idempotent)")

print("\nALL PHASE 2 CHECKS PASSED")
