"""Probe direct Garmin endpoints and dump raw shapes to data/samples/garmin_*.json,
so the parser is written against real data. Run AFTER garmin_auth.py (uses the cached
token; no MFA prompt needed once authenticated).

Run:  .\.venv\Scripts\python.exe scripts\garmin_probe.py
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import BASE, env
from connectors.garmin import connect

SAMPLES = BASE / "data" / "samples"


def main():
    SAMPLES.mkdir(parents=True, exist_ok=True)
    g = connect(env("GARMIN_EMAIL"), env("GARMIN_PASSWORD"),
                prompt_mfa=lambda: input("Garmin MFA code (if prompted): ").strip())
    print(f"Logged in as {g.get_full_name()}")
    d = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    probes = {
        "stress": lambda: g.get_all_day_stress(d),
        "spo2": lambda: g.get_spo2_data(d),
        "body_battery": lambda: g.get_body_battery(d, d),
        "training_readiness": lambda: g.get_training_readiness(d),
        "hrv": lambda: g.get_hrv_data(d),
        "respiration": lambda: g.get_respiration_data(d),
        "max_metrics": lambda: g.get_max_metrics(d),
        "user_summary": lambda: g.get_user_summary(d),
    }
    for name, fn in probes.items():
        try:
            data = fn()
            (SAMPLES / f"garmin_{name}.json").write_text(
                json.dumps(data, indent=2, default=str), encoding="utf-8")
            summary = (list(data.keys())[:10] if isinstance(data, dict)
                       else f"list[{len(data)}]" if isinstance(data, list) else type(data).__name__)
            print(f"  {name}: OK  {summary}")
        except Exception as e:  # noqa: BLE001
            print(f"  {name}: ERR {type(e).__name__}: {e}")
    print(f"\nRaw shapes saved to {SAMPLES}")


if __name__ == "__main__":
    main()
