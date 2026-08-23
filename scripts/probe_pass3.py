"""Probe Garmin all-day HR and an Intervals activity HR stream for Pass 3 shapes."""
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests

from config import BASE, DB_PATH, env
from connectors.garmin import connect

S = BASE / "data" / "samples"
S.mkdir(parents=True, exist_ok=True)
d = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

g = connect(env("GARMIN_EMAIL"), env("GARMIN_PASSWORD"))
try:
    hr = g.get_heart_rates(d)
    (S / "garmin_heart_rates.json").write_text(json.dumps(hr, indent=2, default=str), encoding="utf-8")
    print("heart_rates:", list(hr.keys()) if isinstance(hr, dict) else type(hr).__name__)
except Exception as e:  # noqa: BLE001
    print("hr err:", e)

con = sqlite3.connect(DB_PATH)
aid = con.execute("SELECT id, sport FROM activity ORDER BY start_time DESC LIMIT 1").fetchone()
con.close()
if aid:
    r = requests.get(f"https://intervals.icu/api/v1/activity/{aid[0]}/streams",
                     params={"types": "heartrate,time"}, headers={"Accept": "application/json"},
                     auth=("API_KEY", env("INTERVALS_API_KEY")), timeout=30)
    (S / "intervals_stream.json").write_text(r.text, encoding="utf-8")
    print(f"stream {aid[1]} {aid[0]}: HTTP {r.status_code}, {len(r.text)} chars")
