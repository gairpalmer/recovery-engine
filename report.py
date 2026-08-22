"""Print the latest recovery readiness and a short trend.

Run:  .\.venv\Scripts\python.exe report.py
"""
import sqlite3

from config import DB_PATH


def main():
    con = sqlite3.connect(DB_PATH)
    try:
        rows = con.execute(
            """SELECT day, score, hrv_sub, sleep_sub, rhr_sub, load_sub, debt_sub,
                      ctl, atl, form, hrv_source, sleep_source, drivers, flags
               FROM readiness ORDER BY day DESC LIMIT 14""").fetchall()
        form_swims = con.execute(
            """SELECT start_time, avg_hr FROM activity
               WHERE lower(sport) LIKE 'swim%' AND source IS NOT NULL
                 AND source <> 'GARMIN_CONNECT' ORDER BY start_time DESC""").fetchall()
    finally:
        con.close()

    if not rows:
        print("No readiness computed yet. Run pull.py then engine.py.")
        return

    if form_swims:
        st, hr = form_swims[0]
        print("\n  *** FORM SWIM DATA IS LIVE *** "
              f"{len(form_swims)} FORM swim(s); latest {st[:10]} avgHR {hr}")
        print("  (goggle HR now supersedes the Garmin wrist reading for swim load)")

    d = rows[0]
    print(f"\n=== Recovery readiness -- {d[0]} ===")
    print(f"  SCORE  {d[1]:.0f}/100")
    print(f"  {d[12]}")
    if d[13]:
        print(f"  ! {d[13]}")
    print(f"  fitness(CTL) {d[7]:.0f}   fatigue(ATL) {d[8]:.0f}   form {d[9]:+.0f}")
    print(f"  components: HRV {d[2]:.0f}  sleep {d[3]:.0f}  RHR {d[4]:.0f}  "
          f"load {d[5]:.0f}  debt {d[6]:.0f}   (/100)")
    print(f"  sources: HRV={d[10]}  sleep={d[11]}")

    print("\n  recent:")
    for r in reversed(rows):
        bar = "#" * int(round((r[1] or 0) / 5))
        print(f"   {r[0]}  {r[1]:5.0f}  {bar}")


if __name__ == "__main__":
    main()
