"""Quick sanity checks for the Phase 1 core (no external deps required)."""

import sqlite3

from daylog.config import get_config
from daylog.util import human_duration, iso, parse_iso, utcnow

cfg = get_config()
print("data_dir:", cfg.storage.data_dir)
print("db_path :", cfg.db_path)
print("vault   :", cfg.obsidian.vault_path)
print("exclude :", cfg.capture.exclude_apps)

conn = sqlite3.connect(cfg.db_path)
tables = [r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
)]
print("tables  :", tables)

expected = {
    "daily_reports", "events", "screenshots", "segments",
    "sync_state", "tasks", "terminal_cmds",
}
missing = expected - set(tables)
assert not missing, f"missing tables: {missing}"

# time helpers round-trip
now = utcnow()
assert parse_iso(iso(now)).replace(microsecond=0) == now.replace(microsecond=0)
assert human_duration(90) == "1m 30s"
assert human_duration(3661) == "1h 1m"
assert human_duration(45) == "45s"

print("OK: schema + config + time helpers all good")
