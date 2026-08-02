"""Validate Claude Code transcript ingestion (synthetic + live against real logs)."""

import json
import tempfile
from datetime import datetime, time, timezone
from pathlib import Path

from daylog import claude_ingest
from daylog.config import ClaudeCodeConfig, Config

# --- synthetic transcript ---
tmp = Path(tempfile.mkdtemp())
proj = tmp / "D--Projects-Kundan"; proj.mkdir()
today = datetime.now().date()
noon_utc = datetime.combine(today, time(12, 0)).astimezone(timezone.utc).isoformat()
date_str = today.strftime("%Y-%m-%d")

lines = [
    {"type": "ai-title", "aiTitle": "Fix null error in portfolio editor"},
    {"type": "user", "message": {"role": "user", "content": "fix the toLowerCase null error when closing a tab"},
     "origin": {"kind": "human"}, "timestamp": noon_utc, "cwd": "D:\\Projects\\Kundan\\heyitskundan"},
    {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": "Edit", "input": {"file_path": "D:\\x\\editor.tsx"}}]},
     "timestamp": noon_utc},
    {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": "npm run build"}}]},
     "timestamp": noon_utc},
    {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "x", "is_error": True, "content": "build failed"}]},
     "timestamp": noon_utc},
    {"type": "user", "message": {"role": "user", "content": "/clear"},
     "origin": {"kind": "human"}, "timestamp": noon_utc},   # slash command -> ignored
]
(proj / "sess1.jsonl").write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")

cfg = Config(claude_code=ClaudeCodeConfig(enabled=True, logs_dir=tmp))
sessions = claude_ingest.sessions_for_day(cfg, date_str)
assert len(sessions) == 1, sessions
s = sessions[0]
assert s["title"] == "Fix null error in portfolio editor", s["title"]
assert s["project"] == "heyitskundan", s["project"]
assert s["edits"] == 1 and len(s["bash"]) == 1 and s["errors"] == 1, s
assert "editor.tsx" in s["files"], s["files"]
assert any("toLowerCase" in p for p in s["prompts"]), s["prompts"]
assert all(not p.startswith("/") for p in s["prompts"]), "slash commands filtered"

block = claude_ingest.render_context(sessions)
assert "CLAUDE CODE SESSIONS" in block and "Fix null error" in block and "asked:" in block
print("OK (synthetic): parses title, prompts, edits/bash/errors, files; filters slash commands")

# --- live: real logs for 2026-06-23 ---
live = claude_ingest.sessions_for_day(Config(), "2026-06-23")
print(f"live: {len(live)} Claude Code session(s) on 2026-06-23")
for s in live[:5]:
    print(f"  - {s['title'] or '(untitled)'}  [{s['project']}]  "
          f"edits={s['edits']} bash={len(s['bash'])} errors={s['errors']} prompts={len(s['prompts'])}")
print("OK: live ingestion ran")
