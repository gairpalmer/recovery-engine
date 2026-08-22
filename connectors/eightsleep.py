"""Eight Sleep connector -- READ ONLY.

Uses the vendored OAuth2 pyEight (project-root `pyeight/` package). This deliberately
does NOT call EightSleep.update_user()/update_user_data(): those run alarm discovery
which POSTs test alarms to the bed. We only call start() and update_trend_data(),
both pure GETs, so nothing is ever written to your Eight Sleep account.

Night trend shape (from pyeight/user.py): each day dict carries `day`, `score`,
`sleepDuration`/`presenceDuration`/`light|deep|remDuration` (seconds), and a nested
`sleepQualityScore` with hrv / heartRate / respiratoryRate {average, current}.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from pyeight.eight import EightSleep


def _nested(d, *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


def _mins(sec):
    return round(sec / 60, 1) if isinstance(sec, (int, float)) else None


def parse_night(trend: dict) -> dict:
    """Flatten one Eight Sleep trend day into our night row."""
    sleep_dur = trend.get("sleepDuration")
    presence = trend.get("presenceDuration")
    awake = presence - sleep_dur if (isinstance(presence, (int, float))
                                     and isinstance(sleep_dur, (int, float))) else None
    efficiency = (round(100 * sleep_dur / presence, 1)
                  if (isinstance(sleep_dur, (int, float)) and presence) else None)
    return {
        "night_date": trend.get("day"),
        "sleep_score": trend.get("score"),
        "total_sleep_min": _mins(sleep_dur),
        "deep_min": _mins(trend.get("deepDuration")),
        "rem_min": _mins(trend.get("remDuration")),
        "light_min": _mins(trend.get("lightDuration")),
        "awake_min": _mins(awake),
        "efficiency": efficiency,
        "hrv_ms": (_nested(trend, "sleepQualityScore", "hrv", "average")
                   or _nested(trend, "sleepQualityScore", "hrv", "current")),
        "resting_hr": _nested(trend, "sleepQualityScore", "heartRate", "average"),
        "breath_rate": _nested(trend, "sleepQualityScore", "respiratoryRate", "average"),
        "bed_temp": _nested(trend, "sleepQualityScore", "tempBedC", "average"),
    }


async def _cleanup(eight):
    """Close both HTTP clients the library opens and disarm its atexit hook, so the
    Windows event loop does not emit spurious 'Event loop is closed' warnings."""
    import atexit as _atexit
    for step in (
        lambda: _atexit.unregister(eight.at_exit),
        lambda: getattr(eight, "_httpx_client", None) and eight._httpx_client.aclose(),
        eight.stop,
    ):
        try:
            res = step()
            if hasattr(res, "__await__"):
                await res
        except Exception:  # noqa: BLE001
            pass
    await asyncio.sleep(0.25)                      # let transports finish closing


async def _fetch(email, password, tz, days_back):
    eight = EightSleep(email, password, tz)
    try:
        await eight.start()                       # GETs only: token, devices, users
        user = eight.users.get(eight.user_id) or next(iter(eight.users.values()))
        now = datetime.now()
        frm = (now - timedelta(days=days_back)).strftime("%Y-%m-%d")
        to = (now + timedelta(days=1)).strftime("%Y-%m-%d")
        await user.update_trend_data(frm, to)     # GET /users/{id}/trends
        result = [{"raw": t, "parsed": parse_night(t)}
                  for t in user.trends if t.get("day")]
    finally:
        await _cleanup(eight)
    return result


def fetch_nights(email, password, tz="Europe/London", days_back=60):
    """Sync wrapper. Returns a list of {'raw': <trend>, 'parsed': <night row>}."""
    return asyncio.run(_fetch(email, password, tz, days_back))
