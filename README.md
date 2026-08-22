# Recovery Engine

Recovery/readiness metrics computed from Eight Sleep + FORM + Garmin, outside Garmin.
See `PLAN.md` for the design.

## One-time setup

```powershell
cd C:\Users\rwgpa\recovery-engine
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python init_db.py
Copy-Item .env.example .env       # then edit .env with your details
```

## Credentials to put in `.env`

1. **Eight Sleep** — your app email + password, and `EIGHTSLEEP_TZ=Europe/London`.
   Nothing else; the library ships default client credentials.
2. **Garmin** — your Garmin Connect email + password. First run does an interactive
   login (may ask for an MFA code) and caches a token, so the password is used once.
3. **Strava** (for FORM swims) —
   - Go to https://www.strava.com/settings/api, create an app (any name, callback
     `localhost`), copy the **Client ID** and **Client Secret** into `.env`.
   - Run `python scripts/strava_auth.py` (added in Phase 1) once to authorise and
     capture `STRAVA_REFRESH_TOKEN`.
   - In the Strava app, confirm your FORM swims are set to show heart rate.

## Security

`.env` and `data/*.db` are git-ignored and must never sync to OneDrive. Treat the
credentials like the gov logins: local only.
