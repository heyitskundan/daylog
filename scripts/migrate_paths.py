"""Rewrite absolute screenshot paths in the DB after moving the data dir.

Usage: uv run scripts/migrate_paths.py OLD_DIR NEW_DIR [DB_PATH]
DB_PATH defaults to <NEW_DIR>/daylog.db.
"""

import sqlite3
import sys
from pathlib import Path

if len(sys.argv) < 3:
    print(__doc__)
    sys.exit(1)

OLD = sys.argv[1]
NEW = sys.argv[2]
DB = sys.argv[3] if len(sys.argv) > 3 else str(Path(NEW) / "daylog.db")

conn = sqlite3.connect(DB)
before = conn.execute(
    "SELECT COUNT(*) FROM screenshots WHERE path LIKE ?", (OLD + "%",)
).fetchone()[0]
conn.execute(
    "UPDATE screenshots SET path = replace(path, ?, ?), "
    "thumb_path = replace(thumb_path, ?, ?)",
    (OLD, NEW, OLD, NEW),
)
conn.commit()
stale = conn.execute(
    "SELECT COUNT(*) FROM screenshots WHERE path LIKE ?", (OLD + "%",)
).fetchone()[0]
fixed = conn.execute(
    "SELECT COUNT(*) FROM screenshots WHERE path LIKE ?", (NEW + "%",)
).fetchone()[0]
print(f"rows pointing at C before: {before}")
print(f"rows pointing at C after : {stale}")
print(f"rows pointing at D now   : {fixed}")
conn.close()
sys.exit(0 if stale == 0 else 1)
