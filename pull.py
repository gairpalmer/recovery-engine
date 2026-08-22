"""Ingest from Intervals.icu (Garmin wellness + activities + FORM swims) and Eight
Sleep into SQLite. Pure ingestion -- readiness maths live in engine.py.

Run:  .\.venv\Scripts\python.exe pull.py [days_back]
"""
import json
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone

from config import DB_PATH, env
from connectors import eightsleep, intervals


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _raw(con, source, kind, ref_date, payload):
    cur = con.execute(
        "INSERT INTO raw_pull(source, kind, ref_date, pulled_at, payload) VALUES (?,?,?,?,?)",
        (source, kind, ref_date, _now_iso(), json.dumps(payload, default=str)))
    return cur.lastrowid


def pull_intervals(con, days_back):
    key = env("INTERVALS_API_KEY")
    ath = env("INTERVALS_ATHLETE_ID", "0")
    if not key:
        print("  Intervals: no API key, skipping")
        return
    newest = date.today().isoformat()
    oldest = (date.today() - timedelta(days=days_back)).isoformat()

    well = intervals.wellness(key, oldest, newest, ath)
    for w in well:
        rid = _raw(con, "intervals", "wellness", w.get("id"), w)
        con.execute("""INSERT OR REPLACE INTO wellness_daily
            (day, source, hrv_ms, resting_hr, sleep_secs, sleep_score, steps, vo2max,
             ctl_icu, atl_icu, raw_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (w.get("id"), "intervals", w.get("hrv"), w.get("restingHR"),
             w.get("sleepSecs"), w.get("sleepScore"), w.get("steps"), w.get("vo2max"),
             w.get("ctl"), w.get("atl"), rid))
    print(f"  Intervals wellness: {len(well)} days")

    acts = intervals.activities(key, oldest, newest, ath)
    for a in acts:
        start = a.get("start_date_local")
        rid = _raw(con, "intervals", "activity", (start or "")[:10], a)
        con.execute("""INSERT OR REPLACE INTO activity
            (id, start_time, day, sport, source, device, duration_min, avg_hr, max_hr,
             distance_m, superseded, trimp, icu_load, raw_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,0,NULL,?,?)""",
            (a.get("id"), start, (start or "")[:10], a.get("type"), a.get("source"),
             a.get("device_name"),
             (a.get("moving_time") or a.get("elapsed_time") or 0) / 60.0,
             a.get("average_heartrate"), a.get("max_heartrate"), a.get("distance"),
             a.get("icu_training_load"), rid))
    print(f"  Intervals activities: {len(acts)}")


def pull_eightsleep(con, days_back):
    email, pw = env("EIGHTSLEEP_EMAIL"), env("EIGHTSLEEP_PASSWORD")
    if not email or not pw:
        print("  Eight Sleep: no creds, skipping")
        return
    tz = env("EIGHTSLEEP_TZ", "Europe/London")
    try:
        nights = eightsleep.fetch_nights(email, pw, tz, days_back)
    except Exception as e:  # noqa: BLE001
        print(f"  Eight Sleep: FAILED ({type(e).__name__}: {e})")
        return
    for n in nights:
        p, raw = n["parsed"], n["raw"]
        rid = _raw(con, "eightsleep", "trend", p["night_date"], raw)
        con.execute("""INSERT OR REPLACE INTO eightsleep_night
            (night_date, sleep_score, total_sleep_min, deep_min, rem_min, light_min,
             awake_min, efficiency, hrv_ms, resting_hr, breath_rate, raw_id)
            VALUES (:night_date,:sleep_score,:total_sleep_min,:deep_min,:rem_min,
             :light_min,:awake_min,:efficiency,:hrv_ms,:resting_hr,:breath_rate,:raw_id)""",
            {**p, "raw_id": rid})
    print(f"  Eight Sleep: {len(nights)} nights")


def main(days_back=120):
    con = sqlite3.connect(DB_PATH)
    try:
        print("Pulling...")
        pull_intervals(con, days_back)
        pull_eightsleep(con, min(days_back, 60))
        con.commit()
    finally:
        con.close()
    print("Pull complete.")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 120)
