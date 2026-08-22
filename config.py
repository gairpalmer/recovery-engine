"""Central config: paths, secrets, tunable defaults."""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
DATA_DIR = BASE / "data"
DB_PATH = DATA_DIR / "recovery.db"

load_dotenv(BASE / ".env")


def env(key: str, default=None):
    return os.environ.get(key, default)


# Seeded into the `settings` table on first init; edit there afterwards, not here.
DEFAULT_SETTINGS = {
    # Physiology  -- hr_max is a PLACEHOLDER: set to a lab value, 220-age, or let the
    # engine adopt the max observed HR across activities. hr_rest auto-updates nightly.
    "hr_max": "185",
    "hr_rest": "50",
    "sleep_need_min": "480",          # nightly sleep need, minutes (8h)
    # Readiness weights (must sum to 1.0)
    "w_hrv": "0.40",
    "w_sleep": "0.25",
    "w_rhr": "0.10",
    "w_load": "0.15",
    "w_debt": "0.10",
    # Baselines / thresholds
    "hrv_baseline_days": "60",
    "rhr_baseline_days": "60",
    "hrv_divergence_pct": "15",       # ES vs Garmin HRV gap that raises a flag
    "ctl_days": "42",
    "atl_days": "7",
}
