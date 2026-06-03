from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/bot.sqlite3")
    with sqlite3.connect(path) as conn:
        for table in ("inbound_jobs", "chat_history"):
            print(f"== {table} ==")
            for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1 DESC LIMIT 10"):
                print(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
