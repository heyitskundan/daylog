import json

from daylog import llm
from daylog.label import (
    _clean_name, _clean_steps, _clean_text, _is_degenerate, label_day, propose_tasks_builtin,
)
from daylog.segment import segment_day

from conftest import add_window_event


def test_clean_name_strips_leaked_field_labels():
    assert _clean_name("name: 'Configure and run daylog project'") == "Configure and run daylog project"
    assert _clean_name('"  Set up Cloudflare  "') == "Set up Cloudflare"


def test_clean_name_falls_back_to_placeholder_on_empty():
    assert _clean_name("") == "Untitled task"


def test_clean_text_salvages_head_before_runaway_number_spew():
    runaway = "Navigated to the daylog project directory " + ", ".join(str(n) for n in range(6, 247))
    cleaned = _clean_text(runaway)
    assert cleaned == "Navigated to the daylog project directory"
    assert not _is_degenerate(cleaned)


def test_is_degenerate_detects_number_and_word_spam():
    assert _is_degenerate("1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12")
    assert _is_degenerate("done done done done done done")
    assert not _is_degenerate("Fixed the DNS record")


def test_clean_steps_keeps_good_drops_junk_dedupes_and_caps():
    steps = [
        "Opened the Zero Trust dashboard",
        "Navigated to the daylog project directory " + ", ".join(str(n) for n in range(1, 200)),
        "1 2 3 4 5 6 7 8 9 10 11 12 13",
        "Opened the Zero Trust dashboard",  # dup
        "Fixed the DNS record",
    ]
    out = _clean_steps(steps)
    assert "Opened the Zero Trust dashboard" in out
    assert "Fixed the DNS record" in out
    assert "Navigated to the daylog project directory" in out
    assert out.count("Opened the Zero Trust dashboard") == 1
    assert all(not _is_degenerate(s) for s in out)
    assert len(out) <= 8


def test_propose_tasks_builtin_groups_by_project_and_flags_struggle(conn, cfg, base_time, date_str):
    for i in range(12):
        add_window_event(conn, base_time, i, 60, "chrome.exe", "Cloudflare Zero Trust dashboard")
    for i in range(15):
        add_window_event(conn, base_time, 12 + i, 60, "Code.exe", "tunnel.py - valuelyne - Visual Studio Code")
    conn.commit()

    segs = segment_day(conn, cfg, date_str)
    rows = conn.execute("SELECT * FROM segments ORDER BY ts_start").fetchall()
    tasks = propose_tasks_builtin(conn, rows)

    assert len(tasks) >= 1
    total_secs = sum(
        rows[i]["active_secs"] for t in tasks for i in t["segment_indexes"]
    )
    assert total_secs == sum(s.active_secs for s in segs)
    assert all("segment_indexes" in t for t in tasks)


def test_label_day_uses_builtin_labeler_when_no_ai_available(conn, cfg, base_time, date_str, monkeypatch):
    add_window_event(conn, base_time, 0, 600, "Code.exe", "a.py - valuelyne - VS Code")
    conn.commit()
    segment_day(conn, cfg, date_str)

    monkeypatch.setattr(llm, "availability", lambda _cfg: (False, "no provider configured"))

    tasks = label_day(conn, cfg, date_str)
    assert len(tasks) >= 1
    rows = conn.execute("SELECT status FROM tasks WHERE date=?", (date_str,)).fetchall()
    assert all(r["status"] == "proposed" for r in rows)


def test_label_day_with_fake_llm_groups_and_persists(conn, cfg, base_time, date_str, monkeypatch):
    for i in range(12):
        add_window_event(conn, base_time, i, 60, "chrome.exe", "Cloudflare Zero Trust dashboard")
    for i in range(15):
        add_window_event(conn, base_time, 12 + i, 60, "Code.exe", "tunnel.py - valuelyne - Visual Studio Code")
    conn.commit()
    segment_day(conn, cfg, date_str)

    def fake_complete_json(_cfg, _system, _user, _schema):
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

    monkeypatch.setattr(llm, "availability", lambda _cfg: (True, "fake"))
    monkeypatch.setattr(llm, "complete_json", fake_complete_json)

    tasks = label_day(conn, cfg, date_str)
    assert len(tasks) == 2
    cloudflare = [t for t in tasks if "Cloudflare" in t["name"]][0]
    assert cloudflare["struggled"] and cloudflare["total_secs"] > 0

    rows = conn.execute(
        "SELECT name, steps FROM tasks WHERE date=? ORDER BY total_secs DESC", (date_str,)
    ).fetchall()
    steps = json.loads(rows[0]["steps"])
    assert steps["timed"], "timed breakdown should be derived from the matched segments"


def test_label_day_returns_empty_with_no_segments(conn, cfg, date_str):
    assert label_day(conn, cfg, date_str) == []
