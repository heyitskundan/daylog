from daylog.segment import segment_day

from conftest import add_afk_event, add_web_event, add_window_event


def test_afk_time_is_subtracted_from_active_time(conn, cfg, base_time, date_str):
    # One window "focused" for 8 hours (merged), but the user was AFK for 7 of those hours.
    add_window_event(conn, base_time, 0, 8 * 3600, "Code.exe", "main.py - daylog - VS Code")
    add_afk_event(conn, base_time, 30, 7 * 3600)  # 10:30 -> 17:30 away
    conn.commit()

    segs = segment_day(conn, cfg, date_str)

    assert len(segs) == 1
    active_min = segs[0].active_secs / 60
    idle_min = segs[0].idle_secs / 60
    assert 55 <= active_min <= 65
    assert 415 <= idle_min <= 425
    assert segs[0].active_secs <= 8 * 3600


def test_afk_persisted_to_segments_table(conn, cfg, base_time, date_str):
    add_window_event(conn, base_time, 0, 3600, "Code.exe", "a.py - proj - VS Code")
    segment_day(conn, cfg, date_str)
    rows = conn.execute("SELECT active_secs FROM segments").fetchall()
    assert len(rows) == 1
    assert rows[0]["active_secs"] > 0


def _build_realistic_day(conn, base):
    # Block 1: focused coding, 26 minutes (long dwell).
    for i in range(26):
        add_window_event(conn, base, i, 60, "Code.exe", "main.py - valuelyne - Visual Studio Code")
    # Block 2: research churn on stackoverflow, lots of title flips, 8 minutes.
    add_web_event(conn, base, 26, "https://stackoverflow.com/questions/123")
    for i in range(40):
        add_window_event(conn, base, 26 + i * 0.2, 12, "chrome.exe", f"python error fix {i} - Stack Overflow")
    # Block 3: back to coding.
    for i in range(10):
        add_window_event(conn, base, 40 + i, 60, "Code.exe", "db.py - valuelyne - Visual Studio Code")
    conn.commit()


def test_segmentation_groups_by_app_and_title_change(conn, cfg, base_time, date_str):
    _build_realistic_day(conn, base_time)
    segments = segment_day(conn, cfg, date_str)

    assert len(segments) >= 3
    apps = [s.primary_app for s in segments]
    assert "Code.exe" in apps and "chrome.exe" in apps


def test_long_dwell_signal_set_on_sustained_block(conn, cfg, base_time, date_str):
    _build_realistic_day(conn, base_time)
    segments = segment_day(conn, cfg, date_str)
    assert any(s.signals.get("long_dwell") for s in segments)


def test_research_host_signal_only_on_browser_block(conn, cfg, base_time, date_str):
    _build_realistic_day(conn, base_time)
    segments = segment_day(conn, cfg, date_str)

    chrome = [s for s in segments if s.primary_app == "chrome.exe"][0]
    assert chrome.signals.get("research_host") is True
    assert chrome.url_host == "stackoverflow.com"

    # Coding blocks should NOT inherit the browser host (half-open boundary handling).
    code_segs = [s for s in segments if s.primary_app == "Code.exe"]
    assert all(not s.signals.get("research_host") for s in code_segs)


def test_project_guess_parsed_from_editor_title(conn, cfg, base_time, date_str):
    _build_realistic_day(conn, base_time)
    segments = segment_day(conn, cfg, date_str)
    code = [s for s in segments if s.primary_app == "Code.exe"][0]
    assert code.project_guess == "valuelyne"


def test_active_time_never_exceeds_wall_clock_span(conn, cfg, base_time, date_str):
    add_window_event(conn, base_time, 0, 600, "Code.exe", "a.py - proj - VS Code")
    segs = segment_day(conn, cfg, date_str)
    from daylog.util import parse_iso

    for s in segs:
        span = (parse_iso(s.ts_end) - parse_iso(s.ts_start)).total_seconds()
        assert s.active_secs <= int(span)


def test_idle_gap_starts_a_new_segment(conn, cfg, base_time, date_str):
    add_window_event(conn, base_time, 0, 60, "Code.exe", "a.py - proj - VS Code")
    # A gap much larger than the default idle_gap_seconds (180s) with no AFK row between.
    add_window_event(conn, base_time, 30, 60, "Code.exe", "a.py - proj - VS Code")
    segs = segment_day(conn, cfg, date_str)
    assert len(segs) == 2
