import json
from datetime import datetime, time, timezone

from daylog import claude_ingest
from daylog.config import ClaudeCodeConfig, Config


def _write_transcript(proj_dir, noon_utc):
    lines = [
        {"type": "ai-title", "aiTitle": "Fix null error in portfolio editor"},
        {"type": "user", "message": {"role": "user",
         "content": "fix the toLowerCase null error when closing a tab"},
         "origin": {"kind": "human"}, "timestamp": noon_utc,
         "cwd": "D:\\Projects\\Kundan\\heyitskundan"},
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
         "origin": {"kind": "human"}, "timestamp": noon_utc},  # slash command -> ignored
    ]
    (proj_dir / "sess1.jsonl").write_text(
        "\n".join(json.dumps(x) for x in lines), encoding="utf-8"
    )


def test_sessions_for_day_parses_prompts_edits_and_errors(tmp_path):
    proj = tmp_path / "D--Projects-Kundan"
    proj.mkdir()
    today = datetime.now().date()
    noon_utc = datetime.combine(today, time(12, 0)).astimezone(timezone.utc).isoformat()
    date_str = today.strftime("%Y-%m-%d")
    _write_transcript(proj, noon_utc)

    cfg = Config(claude_code=ClaudeCodeConfig(enabled=True, logs_dir=tmp_path))
    sessions = claude_ingest.sessions_for_day(cfg, date_str)

    assert len(sessions) == 1
    s = sessions[0]
    assert s["title"] == "Fix null error in portfolio editor"
    assert s["project"] == "heyitskundan"
    assert s["edits"] == 1
    assert len(s["bash"]) == 1
    assert s["errors"] == 1
    assert "editor.tsx" in s["files"]
    assert any("toLowerCase" in p for p in s["prompts"])
    assert all(not p.startswith("/") for p in s["prompts"])


def test_render_context_includes_session_summary(tmp_path):
    proj = tmp_path / "D--Projects-Kundan"
    proj.mkdir()
    today = datetime.now().date()
    noon_utc = datetime.combine(today, time(12, 0)).astimezone(timezone.utc).isoformat()
    date_str = today.strftime("%Y-%m-%d")
    _write_transcript(proj, noon_utc)

    cfg = Config(claude_code=ClaudeCodeConfig(enabled=True, logs_dir=tmp_path))
    sessions = claude_ingest.sessions_for_day(cfg, date_str)
    block = claude_ingest.render_context(sessions)

    assert "CLAUDE CODE SESSIONS" in block
    assert "Fix null error" in block
    assert "asked:" in block


def test_render_context_empty_when_no_sessions():
    assert claude_ingest.render_context([]) == ""


def test_sessions_for_day_disabled_returns_empty(tmp_path):
    cfg = Config(claude_code=ClaudeCodeConfig(enabled=False, logs_dir=tmp_path))
    assert claude_ingest.sessions_for_day(cfg, "2026-06-23") == []


def test_sessions_for_day_missing_logs_dir_returns_empty(tmp_path):
    cfg = Config(claude_code=ClaudeCodeConfig(enabled=True, logs_dir=tmp_path / "nope"))
    assert claude_ingest.sessions_for_day(cfg, "2026-06-23") == []
