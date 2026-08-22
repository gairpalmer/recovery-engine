"""Show your highest recorded HRs and set the hr_max setting.

Run:  .\.venv\Scripts\python.exe scripts\calibrate_hrmax.py [value]   (default 182)
The engine also auto-adopts any observed peak above the setting.
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import DB_PATH

val = int(sys.argv[1]) if len(sys.argv) > 1 else 182
con = sqlite3.connect(DB_PATH)
try:
    print("Top recorded max HR across your activities:")
    for sport, st, mhr in con.execute(
            """SELECT sport, start_time, max_hr FROM activity
               WHERE max_hr IS NOT NULL ORDER BY max_hr DESC LIMIT 6"""):
        print(f"  {mhr:>3.0f} bpm   {sport} {st[:10]}")
    peak = con.execute("SELECT MAX(max_hr) FROM activity").fetchone()[0] or 0
    con.execute("UPDATE settings SET value=? WHERE key='hr_max'", (str(val),))
    con.commit()
    print(f"\nobserved peak = {peak:.0f} bpm")
    print(f"hr_max setting -> {val};  effective hr_max used = max(setting, peak) = {max(val, peak):.0f}")
finally:
    con.close()
