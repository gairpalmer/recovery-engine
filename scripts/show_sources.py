"""Show where activities came from, and detail every swim. Answers 'is FORM in yet?'

Run:  .\.venv\Scripts\python.exe scripts\show_sources.py
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import DB_PATH

con = sqlite3.connect(DB_PATH)
try:
    print("Activity sources (all activities):")
    for src, dev, n in con.execute(
            "SELECT source, device, COUNT(*) AS n FROM activity GROUP BY source, device ORDER BY n DESC"):
        print(f"  {src or '?'} / {dev or '?'}: {n}")

    print("\nSwims:")
    swims = con.execute(
        """SELECT start_time, source, device, avg_hr, max_hr, superseded
           FROM activity WHERE lower(sport) LIKE 'swim%' ORDER BY start_time DESC""").fetchall()
    if not swims:
        print("  (none)")
    for st, src, dev, ahr, mhr, sup in swims:
        tag = "  [SUPERSEDED by FORM]" if sup else ""
        print(f"  {st}  src={src}  dev={dev}  avgHR={ahr} maxHR={mhr}{tag}")

    non_garmin = con.execute(
        "SELECT COUNT(*) FROM activity WHERE source IS NOT NULL AND source<>'GARMIN_CONNECT'"
    ).fetchone()[0]
    print(f"\nNon-Garmin activities (i.e. FORM etc.): {non_garmin}")
finally:
    con.close()
