import json
from datetime import timedelta, timezone

from daylog.config import Config, ObsidianConfig, StorageConfig
from daylog.segment import segment_day
from daylog.util import iso
from daylog.web import day_stats, render_day, render_search, render_settings, search_ocr

from conftest import add_window_event


def _add_screenshots(conn, base_time):
    for i, src in enumerate(["auto", "manual"]):
        ts = iso((base_time + timedelta(minutes=5 + i)).astimezone(timezone.utc))
        conn.execute(
            "INSERT INTO screenshots(ts, path, thumb_path, app, title, source, ocr_text, ocr_done) "
            "VALUES(?,?,?,?,?,?,?,1)",
            (ts, f"/fake/{i}.jpg", f"/fake/{i}.thumb.jpg", "Code.exe", "app.py - valuelyne", src,
             "cloudflare tunnel token DNS record"),
        )
    conn.commit()


def test_day_stats_aggregates_segments_and_apps(conn, cfg, base_time, date_str):
    for i in range(15):
        add_window_event(conn, base_time, i, 60, "Code.exe", "app.py - valuelyne - Visual Studio Code")
    for i in range(10):
        add_window_event(conn, base_time, 15 + i, 60, "WindowsTerminal.exe", "cloudflared tunnel - pwsh")
    conn.commit()
    segment_day(conn, cfg, date_str)

    stats = day_stats(conn, date_str)
    assert stats["total_active"] > 0
    assert len(stats["segments"]) >= 2
    apps = dict(stats["by_app"])
    assert "Code.exe" in apps and "WindowsTerminal.exe" in apps


def test_day_stats_includes_screenshots(conn, cfg, base_time, date_str):
    _add_screenshots(conn, base_time)
    stats = day_stats(conn, date_str)
    assert len(stats["shots"]) == 2


def test_render_day_shows_time_bars_and_summarize_action(conn, cfg, base_time, date_str):
    for i in range(15):
        add_window_event(conn, base_time, i, 60, "Code.exe", "app.py - valuelyne - Visual Studio Code")
    conn.commit()
    segment_day(conn, cfg, date_str)

    html = render_day(day_stats(conn, date_str))
    assert "Where the time went" in html
    assert "Code.exe" in html
    assert "Summarize this day" in html


def test_render_day_empty_day_shows_nothing_captured(conn):
    empty = render_day(day_stats(conn, "2000-01-01"))
    assert "Nothing captured" in empty


def test_render_day_edit_mode_shows_task_form(conn, cfg, base_time, date_str):
    for i in range(12):
        add_window_event(conn, base_time, i, 60, "Code.exe", "x.py - valuelyne - VS Code")
    conn.commit()
    segs = segment_day(conn, cfg, date_str)
    steps = json.dumps({"ai_steps": ["edit x.py"], "timed": [
        {"label": "valuelyne", "app": "Code.exe", "host": None, "secs": segs[0].active_secs,
         "ts_start": segs[0].ts_start, "ts_end": segs[0].ts_end}], "note": ""})
    conn.execute(
        "INSERT INTO tasks(date,name,summary,total_secs,struggled,learned,status,steps,"
        "screenshot_ids) VALUES(?,?,?,?,0,0,'proposed',?, '[]')",
        (date_str, "Untitled work", "did stuff", segs[0].active_secs, steps),
    )
    conn.commit()

    html = render_day(day_stats(conn, date_str))
    assert "Untitled work" in html
    assert "Publish this day" in html
    assert f'href="/note/{date_str}"' in html

    edit_html = render_day(day_stats(conn, date_str), edit_mode=True)
    assert 'action="/api/task"' in edit_html


def test_render_day_capture_chip_reflects_state(conn):
    day = {"date": "2026-06-24", "segments": [{"active_secs": 300, "primary_app": "Code.exe"}],
           "by_app": [("Code.exe", 300)], "total_active": 300, "shots": [], "tasks": [],
           "entries": [], "lede": ""}
    assert 'action="/api/capture/stop"' in render_day(day, capture_running=True)
    assert 'action="/api/capture/start"' in render_day(day, capture_running=False)
    assert "managed by the tray" in render_day(day, capture_running=None)


def test_search_ocr_and_render_search(conn, cfg, base_time, date_str):
    _add_screenshots(conn, base_time)
    rows = search_ocr(conn, "cloudflare")
    assert len(rows) == 2
    shtml = render_search(rows, "cloudflare")
    assert "match(es)" in shtml and "cloudflare" in shtml.lower()


def test_search_ocr_empty_query_returns_nothing(conn):
    assert search_ocr(conn, "   ") == []


def test_render_settings_shows_all_controls(tmp_path):
    cfg = Config(storage=StorageConfig(data_dir=tmp_path),
                 obsidian=ObsidianConfig(vault_path=tmp_path / "vault", auto_detect=False))
    status = {"watcher": True, "watcher_detail": "Code.exe · browser URLs on",
              "ocr": True, "ocr_detail": "windows engine",
              "ai": True, "ai_detail": "ready", "vault": True, "vault_detail": str(tmp_path)}
    html = render_settings(cfg, status, autostart_on=False)
    for needle in ['name="provider"', 'name="vault_path"', 'name="hotkey"', 'name="data_dir"',
                   "Save settings", "Launch on login", "Activity watcher", "Screenshot text (OCR)"]:
        assert needle in html
    assert "ActivityWatch" not in html
