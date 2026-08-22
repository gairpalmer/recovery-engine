-- Recovery Engine SQLite schema
-- Raw payloads are retained so every derived figure ties back to source.

CREATE TABLE IF NOT EXISTS raw_pull (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    source     TEXT NOT NULL,          -- 'eightsleep' | 'intervals'
    kind       TEXT NOT NULL,          -- 'trend' | 'wellness' | 'activity'
    ref_date   TEXT,
    pulled_at  TEXT NOT NULL,
    payload    TEXT NOT NULL           -- raw JSON
);

-- Eight Sleep nightly (the BETTER sleep/HRV source when connected)
CREATE TABLE IF NOT EXISTS eightsleep_night (
    night_date      TEXT PRIMARY KEY,
    sleep_score     REAL,
    total_sleep_min REAL,
    deep_min        REAL,
    rem_min         REAL,
    light_min       REAL,
    awake_min       REAL,
    efficiency      REAL,
    hrv_ms          REAL,
    resting_hr      REAL,
    breath_rate     REAL,
    bed_temp        REAL,
    raw_id          INTEGER REFERENCES raw_pull(id)
);

-- Daily wellness from Intervals.icu (Garmin-sourced): sleep, RHR, HRV, steps
CREATE TABLE IF NOT EXISTS wellness_daily (
    day         TEXT PRIMARY KEY,       -- YYYY-MM-DD
    source      TEXT,                   -- 'intervals'
    hrv_ms      REAL,
    resting_hr  REAL,
    sleep_secs  REAL,
    sleep_score REAL,
    steps       INTEGER,
    vo2max      REAL,
    ctl_icu     REAL,                   -- Intervals' own fitness, for reference
    atl_icu     REAL,                   -- Intervals' own fatigue, for reference
    raw_id      INTEGER REFERENCES raw_pull(id)
);

-- Unified activities from Intervals (Garmin + FORM), with our own HR-based load
CREATE TABLE IF NOT EXISTS activity (
    id            TEXT PRIMARY KEY,     -- Intervals activity id
    start_time    TEXT NOT NULL,        -- local ISO
    day           TEXT,                 -- YYYY-MM-DD (local)
    sport         TEXT,                 -- Tennis | Swim | Golf | Run | ...
    source        TEXT,                 -- 'GARMIN_CONNECT' | FORM's source tag
    device        TEXT,                 -- e.g. 'Garmin MARQ Golfer'
    duration_min  REAL,
    avg_hr        REAL,
    max_hr        REAL,
    distance_m    REAL,
    superseded    INTEGER DEFAULT 0,    -- 1 = a duplicate swim replaced by FORM
    trimp         REAL,                 -- our Banister HR load
    icu_load      REAL,                 -- Intervals' own load, for reference
    raw_id        INTEGER REFERENCES raw_pull(id)
);

-- Computed daily readiness
CREATE TABLE IF NOT EXISTS readiness (
    day          TEXT PRIMARY KEY,
    score        REAL,                  -- 0-100
    hrv_sub      REAL,
    sleep_sub    REAL,
    rhr_sub      REAL,
    load_sub     REAL,
    debt_sub     REAL,
    ctl          REAL,                  -- our chronic load (fitness)
    atl          REAL,                  -- our acute load (fatigue)
    form         REAL,                  -- ctl - atl
    hrv_source   TEXT,                  -- 'eightsleep' | 'intervals'
    sleep_source TEXT,
    drivers      TEXT,
    flags        TEXT,
    computed_at  TEXT
);

-- Full recovery metric suite (vitals ranges, HRV status, energy, strain, trends)
CREATE TABLE IF NOT EXISTS metrics_daily (
    day             TEXT PRIMARY KEY,
    energy          REAL,   -- Body Battery style reserve now (0-100)
    energy_am       REAL,   -- morning charge
    strain          REAL,   -- 0-21
    recovery_hours  REAL,
    training_status TEXT,
    hrv             REAL, hrv_7d REAL, hrv_mean REAL, hrv_sd REAL, hrv_status TEXT,
    rhr             REAL, rhr_mean REAL, rhr_sd REAL,
    resp            REAL, resp_mean REAL, resp_sd REAL,
    bed_temp        REAL, temp_mean REAL, temp_sd REAL,
    sleep_min       REAL, sleep_mean REAL, sleep_sd REAL,
    sleep_score     REAL, deep_min REAL, rem_min REAL, light_min REAL, awake_min REAL,
    efficiency      REAL,
    vo2max          REAL, steps INTEGER,
    load            REAL, ctl REAL, atl REAL, form REAL,
    readiness       REAL
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
