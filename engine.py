"""Compute HR-based load, CTL/ATL, baselines and daily readiness; write `readiness`.

Design:
  - Each day builds a UNIFIED record that PREFERS Eight Sleep for sleep + HRV (the
    better source) and falls back to Intervals/Garmin.
  - Swims duplicated across Garmin and FORM are de-duplicated with FORM winning
    (better HR): the Garmin copy is marked superseded and dropped from load.
  - Readiness is a weighted blend of sub-scores; weights renormalise over whatever
    inputs a given day actually has, so missing data degrades gracefully.
"""
import math
import sqlite3
from datetime import datetime

import numpy as np
import pandas as pd

from config import DB_PATH


def _settings(con):
    return dict(con.execute("SELECT key, value FROM settings").fetchall())


def _num(d, k, default):
    try:
        return float(d.get(k, default))
    except (TypeError, ValueError):
        return float(default)


def banister_trimp(dur_min, avg_hr, hr_rest, hr_max):
    """Banister HR-based training load (male coefficient)."""
    if not avg_hr or not dur_min or hr_max <= hr_rest:
        return 0.0
    hrr = max(0.0, min(1.0, (avg_hr - hr_rest) / (hr_max - hr_rest)))
    return round(dur_min * hrr * 0.64 * math.exp(1.92 * hrr), 1)


def dedupe_swims(con):
    """Mark a Garmin swim superseded when a non-Garmin (FORM) swim overlaps it."""
    con.execute("UPDATE activity SET superseded=0")
    rows = con.execute(
        "SELECT id, start_time, source FROM activity WHERE lower(sport) LIKE 'swim%'"
    ).fetchall()
    swims = []
    for aid, st, src in rows:
        try:
            swims.append((aid, datetime.fromisoformat(st), src or ""))
        except (TypeError, ValueError):
            continue
    for aid, t, src in swims:
        if src != "GARMIN_CONNECT":
            continue
        for aid2, t2, src2 in swims:
            if src2 and src2 != "GARMIN_CONNECT" and abs((t - t2).total_seconds()) < 2700:
                con.execute("UPDATE activity SET superseded=1 WHERE id=?", (aid,))
                break


def compute_trimp(con, hr_rest, hr_max):
    for aid, dur, hr in con.execute(
            "SELECT id, duration_min, avg_hr FROM activity").fetchall():
        con.execute("UPDATE activity SET trimp=? WHERE id=?",
                    (banister_trimp(dur or 0, hr, hr_rest, hr_max), aid))


def build(con):
    s = _settings(con)
    hr_rest = _num(s, "hr_rest", 50)
    hr_max = _num(s, "hr_max", 185)
    peak = con.execute("SELECT MAX(max_hr) FROM activity").fetchone()[0] or 0
    hr_max = max(hr_max, peak)          # self-calibrate up to any observed peak
    need_min = _num(s, "sleep_need_min", 480)
    weights = {"hrv_sub": _num(s, "w_hrv", .40), "sleep_sub": _num(s, "w_sleep", .25),
               "rhr_sub": _num(s, "w_rhr", .10), "load_sub": _num(s, "w_load", .15),
               "debt_sub": _num(s, "w_debt", .10)}
    base_days = int(_num(s, "hrv_baseline_days", 60))
    ctl_days = _num(s, "ctl_days", 42)
    atl_days = _num(s, "atl_days", 7)
    div_pct = _num(s, "hrv_divergence_pct", 15)

    dedupe_swims(con)
    compute_trimp(con, hr_rest, hr_max)

    load = pd.read_sql_query(
        "SELECT day, SUM(trimp) AS load FROM activity WHERE superseded=0 GROUP BY day",
        con).set_index("day")["load"]
    well = pd.read_sql_query("SELECT * FROM wellness_daily", con).set_index("day")
    es = pd.read_sql_query("SELECT * FROM eightsleep_night", con).set_index("night_date")

    days = sorted(set(well.index) | set(es.index) | set(load.index))
    if not days:
        print("  engine: no data to compute")
        return
    idx = pd.date_range(days[0], days[-1], freq="D").strftime("%Y-%m-%d")

    def col(frame, name):
        return frame[name].reindex(idx) if name in frame.columns else pd.Series(index=idx, dtype=float)

    es_hrv, w_hrv = col(es, "hrv_ms"), col(well, "hrv_ms")
    es_rhr, w_rhr = col(es, "resting_hr"), col(well, "resting_hr")
    es_slp_min = col(es, "total_sleep_min")
    w_slp_min = col(well, "sleep_secs") / 60.0
    es_slp_score, w_slp_score = col(es, "sleep_score"), col(well, "sleep_score")

    df = pd.DataFrame(index=idx)
    df["es_hrv"], df["w_hrv"] = es_hrv, w_hrv
    df["hrv"] = es_hrv.combine_first(w_hrv)
    df["rhr"] = es_rhr.combine_first(w_rhr)
    df["sleep_min"] = es_slp_min.combine_first(w_slp_min)
    df["sleep_score"] = es_slp_score.combine_first(w_slp_score)
    df["hrv_source"] = np.where(es_hrv.notna(), "eightsleep",
                                np.where(w_hrv.notna(), "intervals", None))
    df["sleep_source"] = np.where(es_slp_min.notna() | es_slp_score.notna(), "eightsleep",
                                  np.where(w_slp_min.notna() | w_slp_score.notna(),
                                           "intervals", None))

    # --- training load curves ---
    load_full = load.reindex(idx).fillna(0.0)
    df["ctl"] = load_full.ewm(alpha=1 - math.exp(-1 / ctl_days), adjust=False).mean()
    df["atl"] = load_full.ewm(alpha=1 - math.exp(-1 / atl_days), adjust=False).mean()
    df["form"] = df["ctl"] - df["atl"]

    # --- baselines ---
    hrv_mean = df["hrv"].rolling(base_days, min_periods=8).mean()
    hrv_sd = df["hrv"].rolling(base_days, min_periods=8).std()
    rhr_mean = df["rhr"].rolling(base_days, min_periods=8).mean()
    rhr_sd = df["rhr"].rolling(base_days, min_periods=8).std()
    df["hrv_mean"] = hrv_mean
    df["hrv_pct"] = (df["hrv"] - hrv_mean) / hrv_mean * 100

    # --- sub-scores (0-100) ---
    df["hrv_sub"] = (50 + 22 * (df["hrv"] - hrv_mean) / hrv_sd).clip(0, 100)
    df["rhr_sub"] = (50 - 22 * (df["rhr"] - rhr_mean) / rhr_sd).clip(0, 100)
    df["sleep_sub"] = df["sleep_score"].combine_first(
        (df["sleep_min"] / need_min * 100)).clip(0, 100)
    ratio = df["atl"] / df["ctl"]
    load_sub = (100 - (ratio - 0.8).clip(lower=0) * 80).clip(0, 100)
    df["load_sub"] = load_sub.where(df["ctl"] >= 5, 100.0)   # thin history -> no penalty
    deficit = (need_min - df["sleep_min"]).clip(lower=0)
    df["debt_sub"] = (100 - deficit.rolling(3, min_periods=1).sum() / (3 * need_min) * 150).clip(0, 100)

    # --- weighted readiness, renormalised over available sub-scores ---
    subs = df[list(weights)]
    wser = pd.Series(weights)
    wsum = (subs.notna() * wser).sum(axis=1)
    df["score"] = ((subs.fillna(0) * wser).sum(axis=1) / wsum).where(wsum > 0)

    # ---- extended recovery metric suite ----
    df["load"] = load_full
    df["resp"] = col(es, "breath_rate")
    df["bed_temp"] = col(es, "bed_temp")
    df["deep_min"] = col(es, "deep_min")
    df["rem_min"] = col(es, "rem_min")
    df["light_min"] = col(es, "light_min")
    df["awake_min"] = col(es, "awake_min")
    df["efficiency"] = col(es, "efficiency")
    df["vo2max"] = col(well, "vo2max")
    df["steps"] = col(well, "steps")
    df["hrv_mean"] = hrv_mean
    df["hrv_sd"] = hrv_sd
    df["hrv_7d"] = df["hrv"].rolling(7, min_periods=3).mean()
    df["rhr_mean"] = rhr_mean
    df["rhr_sd"] = rhr_sd
    df["resp_mean"] = df["resp"].rolling(base_days, min_periods=8).mean()
    df["resp_sd"] = df["resp"].rolling(base_days, min_periods=8).std()
    df["temp_mean"] = df["bed_temp"].rolling(base_days, min_periods=8).mean()
    df["temp_sd"] = df["bed_temp"].rolling(base_days, min_periods=8).std()
    df["sleep_mean"] = df["sleep_min"].rolling(base_days, min_periods=8).mean()
    df["sleep_sd"] = df["sleep_min"].rolling(base_days, min_periods=8).std()
    # Energy (Body Battery style): charged by sleep + HRV, drained by the day's load
    df["energy_am"] = (0.6 * df["sleep_sub"] + 0.4 * df["hrv_sub"]).clip(0, 100)
    df["energy"] = (df["energy_am"] - (df["load"].fillna(0) * 0.4).clip(upper=100)).clip(lower=5)
    df["strain"] = (21 * (1 - np.exp(-df["load"].fillna(0) / 80))).round(1)
    df["recovery_hours"] = (df["load"].fillna(0) * 0.4
                            * (1 + (50 - df["hrv_sub"]) / 100)).clip(0, 96).round(0)
    df["hrv_status"] = [_hrv_status(a, b, c)
                        for a, b, c in zip(df["hrv_7d"], df["hrv_mean"], df["hrv_sd"])]
    ctl0 = df["ctl"].shift(7)
    df["training_status"] = [_train_status(a, b, c)
                             for a, b, c in zip(df["ctl"], ctl0, df["form"])]
    df["readiness"] = df["score"]

    _write(con, df, div_pct)
    _write_metrics(con, df)
    print(f"  engine: readiness + metrics computed for {int(df['score'].notna().sum())} days")


def _driver(row):
    parts = []
    if pd.notna(row["hrv_pct"]):
        parts.append(f"HRV {row['hrv_pct']:+.0f}% vs baseline")
    if pd.notna(row["sleep_min"]):
        parts.append(f"sleep {row['sleep_min'] / 60:.1f}h")
    ls = row["load_sub"]
    parts.append("load fresh" if ls >= 80 else "load moderate" if ls >= 60 else "load high")
    return ", ".join(parts)


def _write(con, df, div_pct):
    now = datetime.now().isoformat(timespec="seconds")
    con.execute("DELETE FROM readiness")
    for day, row in df.iterrows():
        if pd.isna(row["score"]):
            continue
        flags = ""
        if pd.notna(row["es_hrv"]) and pd.notna(row["w_hrv"]) and row["w_hrv"]:
            gap = abs(row["es_hrv"] - row["w_hrv"]) / row["w_hrv"] * 100
            if gap > div_pct:
                flags = f"Eight Sleep/Garmin HRV differ {gap:.0f}%"
        con.execute("""INSERT OR REPLACE INTO readiness
            (day, score, hrv_sub, sleep_sub, rhr_sub, load_sub, debt_sub, ctl, atl, form,
             hrv_source, sleep_source, drivers, flags, computed_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (day, round(row["score"], 1),
             _r(row["hrv_sub"]), _r(row["sleep_sub"]), _r(row["rhr_sub"]),
             _r(row["load_sub"]), _r(row["debt_sub"]),
             _r(row["ctl"]), _r(row["atl"]), _r(row["form"]),
             row["hrv_source"], row["sleep_source"], _driver(row), flags, now))


def _r(x):
    return None if pd.isna(x) else round(float(x), 1)


def _hrv_status(h7, mean, sd):
    if pd.isna(h7) or pd.isna(mean) or pd.isna(sd) or sd == 0:
        return None
    if h7 < mean - 0.5 * sd:
        return "Low"
    if h7 > mean + 1.5 * sd:
        return "High"
    return "Balanced"


def _train_status(ctl, ctl0, form):
    if pd.isna(ctl) or pd.isna(ctl0):
        return None
    if pd.notna(form) and form < -30:
        return "Overreaching"
    d = ctl - ctl0
    if d > 1.5:
        return "Productive"
    if d < -1.5:
        return "Detraining"
    return "Maintaining"


def _cell(v):
    if v is None:
        return None
    if isinstance(v, str):
        return v
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else round(f, 2)


_METRIC_COLS = [
    "energy", "energy_am", "strain", "recovery_hours", "training_status",
    "hrv", "hrv_7d", "hrv_mean", "hrv_sd", "hrv_status",
    "rhr", "rhr_mean", "rhr_sd", "resp", "resp_mean", "resp_sd",
    "bed_temp", "temp_mean", "temp_sd", "sleep_min", "sleep_mean", "sleep_sd",
    "sleep_score", "deep_min", "rem_min", "light_min", "awake_min", "efficiency",
    "vo2max", "steps", "load", "ctl", "atl", "form", "readiness",
]


def _write_metrics(con, df):
    con.execute("DELETE FROM metrics_daily")
    ph = ",".join(["?"] * (len(_METRIC_COLS) + 1))
    sql = f"INSERT OR REPLACE INTO metrics_daily (day,{','.join(_METRIC_COLS)}) VALUES ({ph})"
    for day, row in df.iterrows():
        if pd.isna(row.get("hrv")) and pd.isna(row.get("sleep_min")) and pd.isna(row.get("ctl")):
            continue
        con.execute(sql, [day] + [_cell(row.get(c)) for c in _METRIC_COLS])


def main():
    con = sqlite3.connect(DB_PATH)
    try:
        build(con)
        con.commit()
    finally:
        con.close()


if __name__ == "__main__":
    main()
