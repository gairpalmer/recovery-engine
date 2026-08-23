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
             awake_min, efficiency, hrv_ms, resting_hr, breath_rate, bed_temp, raw_id)
            VALUES (:night_date,:sleep_score,:total_sleep_min,:deep_min,:rem_min,
             :light_min,:awake_min,:efficiency,:hrv_ms,:resting_hr,:breath_rate,:bed_temp,:raw_id)""",
            {**p, "raw_id": rid})
    print(f"  Eight Sleep: {len(nights)} nights")


def pull_garmin_direct(con, days=3):
    """Direct Garmin extras via one get_user_summary call per day (stress, Body Battery,
    respiration, intensity, SpO2). Kept to a few recent days -- Garmin rate-limits hard.
    Uses the cached token; fails soft so a hiccup never breaks the pipeline."""
    email, pw = env("GARMIN_EMAIL"), env("GARMIN_PASSWORD")
    if not email or not pw:
        print("  Garmin direct: no creds, skipping")
        return
    try:
        from connectors.garmin import connect
        g = connect(email, pw)           # cached token; no MFA prompt in unattended runs
    except Exception as e:               # noqa: BLE001
        print(f"  Garmin direct: login failed ({type(e).__name__}: {e})")
        return
    n = 0
    for i in range(days):
        d = (date.today() - timedelta(days=i)).isoformat()
        try:
            s = g.get_user_summary(d)
        except Exception as e:           # noqa: BLE001
            print(f"  Garmin {d}: {type(e).__name__}")
            continue
        if not isinstance(s, dict):
            continue
        rid = _raw(con, "garmin", "summary", d, s)
        con.execute("""INSERT OR REPLACE INTO garmin_extra
            (day, stress_avg, stress_max, stress_qualifier, gbb_wake, gbb_recent,
             gbb_high, gbb_low, gbb_charged, gbb_drained, resp_waking,
             intensity_mod, intensity_vig, calories, floors, spo2_avg, spo2_low,
             resting_hr, steps, raw_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (d, s.get("averageStressLevel"), s.get("maxStressLevel"), s.get("stressQualifier"),
             s.get("bodyBatteryAtWakeTime"), s.get("bodyBatteryMostRecentValue"),
             s.get("bodyBatteryHighestValue"), s.get("bodyBatteryLowestValue"),
             s.get("bodyBatteryChargedValue"), s.get("bodyBatteryDrainedValue"),
             s.get("avgWakingRespirationValue"),
             s.get("moderateIntensityMinutes"), s.get("vigorousIntensityMinutes"),
             s.get("totalKilocalories"), s.get("floorsAscended"),
             s.get("averageSpo2"), s.get("lowestSpo2"),
             s.get("restingHeartRate"), s.get("totalSteps"), rid))
        n += 1
    # intraday arrays for today only, for the Body Battery / stress curves
    today = date.today().isoformat()
    for kind, fn in (("body_battery", lambda: g.get_body_battery(today, today)),
                     ("stress", lambda: g.get_all_day_stress(today))):
        try:
            _raw(con, "garmin", kind, today, fn())
        except Exception as e:  # noqa: BLE001
            print(f"  Garmin {kind}: {type(e).__name__}")
    print(f"  Garmin direct: {n} days + intraday")


def main(days_back=60, garmin_days=3):
    con = sqlite3.connect(DB_PATH)
    try:
        print("Pulling...")
        pull_intervals(con, days_back)
        pull_eightsleep(con, min(days_back, 60))
        pull_garmin_direct(con, garmin_days)
        con.commit()
    finally:
        con.close()
    print("Pull complete.")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 60)
