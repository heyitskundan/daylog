from daylog import autostart


def test_write_launcher_creates_a_runnable_entry_point(tmp_path, monkeypatch):
    monkeypatch.setattr(autostart, "PROJECT_ROOT", tmp_path)
    p = autostart.write_launcher()
    assert p.exists()
    assert "daylog.cli tray" in p.read_text(encoding="utf-8")
