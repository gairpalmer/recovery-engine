"""One-time interactive Garmin login. Caches a token so later runs are unattended.

Run from the project root:
    .\.venv\Scripts\python.exe scripts\garmin_auth.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import env
from connectors.garmin import connect, DEFAULT_TOKENSTORE


def main():
    email = env("GARMIN_EMAIL")
    password = env("GARMIN_PASSWORD")
    if not email or not password:
        raise SystemExit("Set GARMIN_EMAIL and GARMIN_PASSWORD in .env first.")
    g = connect(email, password,
                prompt_mfa=lambda: input("Garmin MFA code (if prompted): ").strip())
    print(f"Logged in as {g.get_full_name()}. Token cached at {DEFAULT_TOKENSTORE}.")


if __name__ == "__main__":
    main()
