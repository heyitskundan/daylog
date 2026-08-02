"""Print a day's labeled tasks with their steps. Usage: python scripts/show_day.py 2026-06-23"""

import json
import sqlite3
import sys

from daylog.config import get_config
from daylog.util import human_duration, local_date_str

date = sys.argv[1] if len(sys.argv) > 1 else local_date_str()
c = sqlite3.connect(get_config().db_path)
c.row_factory = sqlite3.Row
rows = c.execute(
    "SELECT name, learned, struggled, total_secs, summary, steps "
    "FROM tasks WHERE date = ? ORDER BY total_secs DESC", (date,)
).fetchall()
print(f"{date}: {len(rows)} task(s)\n")
for t in rows:
    s = json.loads(t["steps"] or "{}")
    flags = " ".join(f for f, v in [("learned", t["learned"]), ("struggled", t["struggled"])] if v)
    print(f"## {t['name']}  ({human_duration(t['total_secs'])}) {('[' + flags + ']') if flags else ''}")
    if t["summary"]:
        print(f"   {t['summary']}")
    for st in s.get("ai_steps", []):
        print(f"   - {st}")
    if s.get("note"):
        print(f"   note: {s['note']}")
    print()
