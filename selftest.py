"""Prove the data taps end-to-end and dump real payload shapes.

Two taps now:
  - Eight Sleep (direct)                -> sleep, HRV, overnight HR
  - Intervals.icu (Garmin + FORM swims) -> wellness + activities

Run (after filling .env):
    .\.venv\Scripts\python.exe selftest.py

Saves raw JSON under data/samples/ so field shapes can be confirmed before the
storage/parse layer is written.
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

from config import BASE, env
from connectors import eightsleep, intervals

SAMPLES = BASE / "data" / "samples"


def _save(name, obj):
    SAMPLES.mkdir(parents=True, exist_ok=True)
    (SAMPLES / f"{name}.json").write_text(
        json.dumps(obj, indent=2, default=str), encoding="utf-8")


def test_eightsleep():
    print("\n=== Eight Sleep ===")
    email, pw = env("EIGHTSLEEP_EMAIL"), env("EIGHTSLEEP_PASSWORD")
    if not email or not pw:
        print("  skipped (no creds in .env)")
        return
    tz = env("EIGHTSLEEP_TZ", "Europe/London")
    nights = eightsleep.fetch_nights(email, pw, tz, days_back=7)
    print(f"  pulled {len(nights)} nights")
    for n in nights[-3:]:
        p = n["parsed"]
        print(f"   {p['night_date']}: score={p['sleep_score']} "
              f"sleep={p['total_sleep_min']}min hrv={p['hrv_ms']} rhr={p['resting_hr']}")
    if nights:
        _save("eightsleep_night_raw", nights[-1]["raw"])
        print("  raw sample -> data/samples/eightsleep_night_raw.json")


def test_intervals():
    print("\n=== Intervals.icu (Garmin wellness + activities + FORM swims) ===")
    key = env("INTERVALS_API_KEY")
    ath = env("INTERVALS_ATHLETE_ID", "0")
    if not key:
        print("  skipped (no INTERVALS_API_KEY in .env)")
        return
    newest = date.today().isoformat()
    well = intervals.wellness(key, (date.today() - timedelta(days=14)).isoformat(), newest, ath)
    print(f"  wellness rows: {len(well)}")
    for w in well[-3:]:
        print(f"   {w.get('id')}: rhr={w.get('restingHR')} hrv={w.get('hrv')} "
              f"sleepSecs={w.get('sleepSecs')} steps={w.get('steps')}")
    acts = intervals.activities(key, (date.today() - timedelta(days=30)).isoformat(), newest, ath)
    swims = [a for a in acts if str(a.get("type", "")).lower().startswith("swim")]
    print(f"  activities 30d: {len(acts)} (swims: {len(swims)})")
    for a in acts[:6]:
        print(f"   {a.get('start_date_local')} {a.get('type')} "
              f"hr={a.get('average_heartrate') or a.get('icu_average_hr')} "
              f"movingSecs={a.get('moving_time')}")
    _save("intervals_wellness", well)
    _save("intervals_activities", acts)
    print("  raw samples -> data/samples/intervals_wellness.json, intervals_activities.json")


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("all", "eightsleep"):
        test_eightsleep()
    if which in ("all", "intervals"):
        test_intervals()
    print("\nDone.")
