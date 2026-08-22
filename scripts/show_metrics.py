"""Print the latest metrics_daily row (the full recovery suite)."""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import DB_PATH

con = sqlite3.connect(DB_PATH)
con.row_factory = sqlite3.Row
try:
    r = con.execute("SELECT * FROM metrics_daily ORDER BY day DESC LIMIT 1").fetchone()
    if not r:
        print("no metrics computed")
    else:
        for k in r.keys():
            print(f"  {k:16}: {r[k]}")
finally:
    con.close()
