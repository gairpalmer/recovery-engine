# Recovery Engine

Garmin-equivalent recovery and readiness metrics, computed **outside** Garmin from
better inputs: Eight Sleep sleep/HRV and FORM swim load, combined with Garmin's
activities, steps and all-day data.

Why outside Garmin: Garmin Connect refuses all external sleep/HRV data inbound, and
its Firstbeat recovery engine is a closed box. The only way to get recovery scores
recalculated from better data is to compute them ourselves.

## Data taps (all verified live, all unofficial)

| Source      | Library / API              | Gives us                                            | Auth |
|-------------|----------------------------|-----------------------------------------------------|------|
| Eight Sleep | `pyeight` (OAuth2 fork), direct | per-minute HR/HRV/breathing, sleep stages, score | email + password + tz (default client creds) |
| Garmin + FORM | Intervals.icu open API (`intervals.icu/api/v1`) | Garmin wellness (sleep/RHR/HRV/steps) + activities (tennis etc.) + FORM swims with HR | free personal API key, Basic auth ("API_KEY", key) |

Why Intervals.icu for Garmin+FORM (decided 2026-08-22): Strava paywalled its API
($11.99/mo, immediate for new devs), and Intervals.icu pulls Garmin natively AND
receives FORM swims, with a FREE key. This also removes the unofficial Garmin API and
the need to store a Garmin password locally. Only Eight Sleep stays a direct pull
(Intervals can't reach a mattress). Garmin's proprietary Body Battery / Training
Readiness do NOT flow through Intervals; add the optional direct Garmin pull
(`connectors/garmin.py`, `scripts/garmin_auth.py`) only if that comparison is wanted.

Unofficial-API risk lives only in the Eight Sleep tap now; the Intervals.icu API is
supported. Connectors isolate each so a break is a one-file fix.

## Readiness model (transparent, tunable — weights live in `settings`)

Daily score 0-100, every term traceable to source:

- **HRV vs baseline** (dominant): overnight HRV z-scored against a rolling 60-day
  baseline. Eight Sleep primary, Garmin HRV cross-checks. Divergence > threshold is
  flagged, not silently averaged.
- **Sleep quality** (Eight Sleep): duration vs need, efficiency, deep+REM share.
- **Resting HR vs baseline**: elevated overnight RHR reduces score.
- **Training-load balance**: acute (7d EWMA) vs chronic (42d EWMA) load. Every
  activity -> HR-based load (Banister TRIMP) so the FORM swim counts toward fatigue.
- **Sleep debt**: cumulative shortfall over last 3 nights.

Output: score + plain-English driver line, e.g.
"HRV 8% under baseline, sleep 7.4h solid, acute load high after yesterday's tennis."

Garmin-equivalent, not Garmin-identical (Firstbeat is proprietary). The point is it
runs on your better inputs.

## Load / TRIMP

Per activity: TRIMP = duration_min x HRR x 0.64 x e^(1.92 x HRR)  (Banister, male)
where HRR = (avg_hr - hr_rest) / (hr_max - hr_rest).
hr_max and hr_rest live in `settings` (hr_rest can auto-update from overnight RHR).
CTL = 42-day EWMA of daily TRIMP; ATL = 7-day EWMA; Form = CTL - ATL.

## Storage

Local SQLite at `data/recovery.db`. Raw source payloads kept in `raw_pull` for
audit/traceability, so every derived number ties back to what the source returned.
Secrets in local-only `.env` (NOT in OneDrive). Garmin token in ~/.garminconnect.

## Phases

- [x] 0. Scaffold + design doc
- [x] 1. Connectors built + import-clean (Eight Sleep read-only, Garmin, Strava) + selftest
- [ ] 0b. YOU authenticate the three services (only you can; no passwords in chat)
- [ ] 1b. Run selftest.py against live accounts -> inspect real payload shapes
- [ ] 2. Storage/parse layer (written against confirmed shapes) + baselines + readiness
- [ ] 3. Morning report + Windows Task Scheduler daily auto-run
- [ ] 4. Local web dashboard + tune against your history

## Run order (once .env is filled)

Default path needs no auth scripts -- just fill .env (Eight Sleep creds +
INTERVALS_API_KEY) then:

```powershell
cd C:\Users\rwgpa\recovery-engine
.\.venv\Scripts\python.exe selftest.py    # prove both taps, dump data/samples/*.json
```

Prereqs in the web UIs (one-time): connect Garmin to Intervals.icu; connect FORM to
Intervals.icu (from the FORM app's Connected Apps); copy the Intervals API key.
Optional direct Garmin: `scripts\garmin_auth.py` (MFA once) only if you want Garmin's
own metrics too.

## Location note

Lives at C:\Users\rwgpa\recovery-engine (local, NOT OneDrive-synced) because it
writes a DB continuously and OneDrive sync races/locks would corrupt that.
