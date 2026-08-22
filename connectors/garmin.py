"""Garmin Connect connector (read-only) via python-garminconnect.

Login reuses a cached token store; the first login calls prompt_mfa for the code
and then persists the token so later runs are unattended.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from garminconnect import Garmin

DEFAULT_TOKENSTORE = str(Path.home() / ".garminconnect")


def connect(email: str, password: str, tokenstore: str = DEFAULT_TOKENSTORE,
            prompt_mfa: Callable[[], str] | None = None) -> Garmin:
    g = Garmin(email, password, prompt_mfa=prompt_mfa)
    g.login(tokenstore)
    return g


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001  -- surface, don't crash the whole pull
        return {"_error": f"{type(e).__name__}: {e}"}


def daily(g: Garmin, cdate: str) -> dict[str, Any]:
    """All wellness data for one date (YYYY-MM-DD). Values are raw Garmin JSON."""
    return {
        "date": cdate,
        "hrv": _safe(g.get_hrv_data, cdate),
        "rhr": _safe(g.get_rhr_day, cdate),
        "body_battery": _safe(g.get_body_battery, cdate, cdate),
        "stress": _safe(g.get_all_day_stress, cdate),
        "steps": _safe(g.get_steps_data, cdate),
        "sleep": _safe(g.get_sleep_data, cdate),
        "readiness": _safe(g.get_training_readiness, cdate),  # Garmin's own, for comparison
    }


def activities(g: Garmin, start: str, end: str) -> list[dict[str, Any]]:
    """Activity summaries between two dates inclusive (YYYY-MM-DD)."""
    try:
        return g.get_activities_by_date(start, end)
    except Exception as e:  # noqa: BLE001
        return [{"_error": f"{type(e).__name__}: {e}"}]
