"""Intervals.icu connector (free personal API key).

One tap for THREE things:
  - Garmin activities (tennis, runs, ...) with HR
  - Garmin wellness (sleep, resting HR, HRV, steps) synced by Intervals from Garmin
  - FORM swims with HR (FORM pushes swims to Intervals.icu natively)

Auth = HTTP Basic, username is the LITERAL string "API_KEY", password is the personal
key from intervals.icu -> Settings -> Developer Settings. Athlete id "0" = key owner.
"""
from __future__ import annotations

from typing import Any

import requests

BASE = "https://intervals.icu/api/v1"
_HEADERS = {"Accept": "application/json"}


def _auth(api_key: str):
    return ("API_KEY", api_key)


def wellness(api_key: str, oldest: str, newest: str, athlete: str = "0") -> list[dict[str, Any]]:
    """Daily wellness rows between two ISO dates inclusive (restingHR, hrv, sleep, steps)."""
    r = requests.get(f"{BASE}/athlete/{athlete}/wellness",
                     params={"oldest": oldest, "newest": newest},
                     headers=_HEADERS, auth=_auth(api_key), timeout=30)
    r.raise_for_status()
    return r.json()


def activities(api_key: str, oldest: str, newest: str, athlete: str = "0") -> list[dict[str, Any]]:
    """Activity summaries between two ISO dates inclusive (type, HR, duration, distance)."""
    r = requests.get(f"{BASE}/athlete/{athlete}/activities",
                     params={"oldest": oldest, "newest": newest},
                     headers=_HEADERS, auth=_auth(api_key), timeout=30)
    r.raise_for_status()
    return r.json()


def streams(api_key: str, activity_id: str, types: str = "heartrate,time") -> list[dict[str, Any]]:
    """Per-sample streams for one activity (list of {type, data}). For in-workout curves."""
    r = requests.get(f"{BASE}/activity/{activity_id}/streams",
                     params={"types": types}, headers=_HEADERS, auth=_auth(api_key), timeout=30)
    r.raise_for_status()
    return r.json()
