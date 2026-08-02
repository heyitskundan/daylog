from daylog import db


def test_init_db_creates_expected_schema(conn):
    tables = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
    }
    expected = {
        "daily_reports", "events", "screenshots", "segments",
        "sync_state", "tasks", "terminal_cmds",
    }
    assert expected <= tables


def test_sync_state_round_trip(conn):
    assert db.get_sync_state(conn, "missing") is None
    db.set_sync_state(conn, "cursor", "42")
    assert db.get_sync_state(conn, "cursor") == "42"
    db.set_sync_state(conn, "cursor", "43")
    assert db.get_sync_state(conn, "cursor") == "43"


def test_transaction_rolls_back_on_error(conn):
    conn.execute("INSERT INTO sync_state(key, value) VALUES('k', 'v')")
    conn.commit()
    try:
        with db.transaction(conn):
            conn.execute("UPDATE sync_state SET value='changed' WHERE key='k'")
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    row = conn.execute("SELECT value FROM sync_state WHERE key='k'").fetchone()
    assert row["value"] == "v"


def test_migrate_adds_purged_column_to_older_schema(tmp_path):
    db_path = tmp_path / "old.db"
    legacy = db.connect(db_path)
    legacy.executescript(
        "CREATE TABLE screenshots (id INTEGER PRIMARY KEY, ts TEXT, path TEXT, "
        "thumb_path TEXT, app TEXT, title TEXT, source TEXT NOT NULL DEFAULT 'auto', "
        "ocr_text TEXT, ocr_done INTEGER NOT NULL DEFAULT 0, embedding_id INTEGER, "
        "deleted INTEGER NOT NULL DEFAULT 0)"
    )
    legacy.commit()
    legacy.close()

    migrated = db.init_db(db_path)
    cols = {r["name"] for r in migrated.execute("PRAGMA table_info(screenshots)").fetchall()}
    assert "purged" in cols
