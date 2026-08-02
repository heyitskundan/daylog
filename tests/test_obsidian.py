import json

from daylog.config import Config, ObsidianConfig, StorageConfig
from daylog.obsidian import publish_day


def _add_task(conn, date, name, secs=600):
    steps = json.dumps({
        "ai_steps": ["did a thing"],
        "timed": [{
            "label": "valuelyne", "app": "Code.exe", "host": None, "secs": secs,
            "ts_start": f"{date}T10:00:00+00:00", "ts_end": f"{date}T10:10:00+00:00",
        }],
        "note": "",
    })
    conn.execute(
        "INSERT INTO tasks(date,name,summary,total_secs,struggled,learned,status,steps,"
        "screenshot_ids) VALUES(?,?,?,?,0,0,'confirmed',?,'[]')",
        (date, name, "summary", secs, steps),
    )
    conn.commit()


def _cfg_with_vault(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    cfg = Config(
        storage=StorageConfig(data_dir=tmp_path),
        obsidian=ObsidianConfig(vault_path=vault, auto_detect=False),
    )
    return cfg, vault


def test_publish_day_creates_organized_date_folder(tmp_path, conn):
    cfg, vault = _cfg_with_vault(tmp_path)
    date = "2026-06-23"
    _add_task(conn, date, "Set up Cloudflare tunnel")
    _add_task(conn, date, "Review PRs")

    publish_day(conn, cfg, date)

    day_dir = vault / "Activity" / "2026-06" / "2026-06-23"
    assert day_dir.is_dir()
    assert (day_dir / "2026-06-23.md").exists()
    assert (day_dir / "attachments").is_dir()

    notes = sorted(p.name for p in day_dir.glob("*.md"))
    assert f"{date} · Set up Cloudflare tunnel.md" in notes
    assert f"{date} · Review PRs.md" in notes
    assert len(notes) == 3

    flat = [p.name for p in (vault / "Activity").glob("*.md")]
    assert flat == []


def test_publish_day_replaces_without_duplicates_on_rename(tmp_path, conn):
    cfg, vault = _cfg_with_vault(tmp_path)
    date = "2026-06-23"
    _add_task(conn, date, "Set up Cloudflare tunnel")
    _add_task(conn, date, "Review PRs")
    publish_day(conn, cfg, date)

    conn.execute(
        "UPDATE tasks SET name='Wire up the Cloudflare tunnel' WHERE name=?",
        ("Set up Cloudflare tunnel",),
    )
    conn.commit()
    publish_day(conn, cfg, date)

    day_dir = vault / "Activity" / "2026-06" / "2026-06-23"
    notes = sorted(p.name for p in day_dir.glob("*.md"))
    assert f"{date} · Set up Cloudflare tunnel.md" not in notes
    assert f"{date} · Wire up the Cloudflare tunnel.md" in notes
    assert len(notes) == 3


def test_publish_day_cleans_up_legacy_flat_files(tmp_path, conn):
    cfg, vault = _cfg_with_vault(tmp_path)
    date = "2026-06-23"
    _add_task(conn, date, "Set up Cloudflare tunnel")
    _add_task(conn, date, "Review PRs")
    publish_day(conn, cfg, date)

    root = vault / "Activity"
    (root / f"{date}.md").write_text("old flat daily", encoding="utf-8")
    (root / f"{date} - Old Task.md").write_text("old flat task", encoding="utf-8")
    old_attach = root / "attachments"
    old_attach.mkdir(exist_ok=True)
    (old_attach / "daylog-1.jpg").write_bytes(b"x")
    conn.execute("UPDATE tasks SET screenshot_ids='[1]' WHERE name='Review PRs'")
    conn.commit()

    publish_day(conn, cfg, date)

    assert not (root / f"{date}.md").exists()
    assert not (root / f"{date} - Old Task.md").exists()
    assert not (old_attach / "daylog-1.jpg").exists()


def test_publish_day_raises_without_tasks(tmp_path, conn):
    from daylog.obsidian import PublishError

    cfg, _ = _cfg_with_vault(tmp_path)
    try:
        publish_day(conn, cfg, "2026-06-23")
        assert False, "expected PublishError"
    except PublishError:
        pass


def test_publish_day_falls_back_to_data_dir_when_no_vault(tmp_path, conn):
    cfg = Config(storage=StorageConfig(data_dir=tmp_path))
    date = "2026-06-23"
    _add_task(conn, date, "Set up Cloudflare tunnel")

    result = publish_day(conn, cfg, date)

    assert (tmp_path / "notes").exists()
    assert result["daily_note"].endswith(f"{date}.md")


def test_daily_note_content_has_summary_and_links(tmp_path, conn):
    cfg, vault = _cfg_with_vault(tmp_path)
    date = "2026-06-23"
    _add_task(conn, date, "Set up Cloudflare tunnel", secs=1200)

    result = publish_day(conn, cfg, date)
    text = (vault / "Activity" / "2026-06" / date / f"{date}.md").read_text(encoding="utf-8")

    assert "Total active" in text
    assert "Set up Cloudflare tunnel" in text
    assert "[[" in text and "]]" in text
    assert result["tasks"] == 1
