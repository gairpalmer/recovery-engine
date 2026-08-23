"""Render the tabbed recovery app to docs/index.html (self-contained, mobile).

Tabs: Today / Sleep / Body / Load / Trends. Intraday Body Battery + stress curves,
Eight Sleep hypnogram and overnight HR/HRV curves, coaching insights, per-section
timestamps and source chips.

Privacy: if DASHBOARD_PASSPHRASE is set, the body is AES-256-GCM encrypted before
writing and decrypted in the browser. Run `dashboard.py preview <path>` for a plaintext
QA copy (never publish plaintext).
"""
import base64
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from config import BASE, DB_PATH, env

DOCS = BASE / "docs"
TZ = ZoneInfo("Europe/London")

SPORT_ICON = {"swim": "\U0001F3CA", "tennis": "\U0001F3BE", "golf": "⛳",
              "run": "\U0001F3C3", "ride": "\U0001F6B4", "walk": "\U0001F6B6",
              "strength": "\U0001F3CB"}
C_HR, C_HRV, C_EN, C_ST = "#f0506e", "#37d67a", "#2fc4c0", "#f0b429"
STAGE_COL = {"awake": "#5a6472", "out": "#5a6472", "rem": "#8b5cf6",
             "light": "#3ba0c4", "deep": "#4062c0"}


def band(score):
    if score is None:
        return ("--", "var(--muted)")
    if score >= 80:
        return ("Prime", "var(--g)")
    if score >= 65:
        return ("Good", "var(--teal)")
    if score >= 50:
        return ("Moderate", "var(--amber)")
    return ("Low", "var(--red)")


def _icon(sport):
    return SPORT_ICON.get((sport or "").lower(), "\U0001F3C5")


def _clock(iso):
    if not iso:
        return ""
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return t.astimezone(TZ).strftime("%H:%M")
    except (ValueError, TypeError):
        return ""


def _chip(text):
    return f'<span class="chip">{text}</span>'


# -------------------------------------------------------------- charts
def _area(pts, color, name, ymin=0, ymax=100, h=96):
    clean = [(float(x), float(y)) for x, y in pts if y is not None and y >= 0]
    if len(clean) < 2:
        return "<p class='muted sm'>No data yet today.</p>"
    xs = [x for x, _ in clean]
    x0, x1 = min(xs), max(xs)
    xr = (x1 - x0) or 1
    w = 320

    def px(x):
        return (x - x0) / xr * w

    def py(y):
        return h - (max(ymin, min(ymax, y)) - ymin) / (ymax - ymin) * h

    ptstr = [f"{px(x):.1f},{py(y):.1f}" for x, y in clean]
    line = " ".join(ptstr)
    area = (f"M {px(clean[0][0]):.1f},{h} L " + " L ".join(ptstr)
            + f" L {px(clean[-1][0]):.1f},{h} Z")
    return (f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" class="area">'
            f'<defs><linearGradient id="{name}" x1="0" x2="0" y1="0" y2="1">'
            f'<stop offset="0" stop-color="{color}" stop-opacity="0.35"/>'
            f'<stop offset="1" stop-color="{color}" stop-opacity="0"/></linearGradient></defs>'
            f'<path d="{area}" fill="url(#{name})"/>'
            f'<polyline points="{line}" fill="none" stroke="{color}" stroke-width="2" '
            f'stroke-linejoin="round"/></svg>')


def _line(series, color, h=70):
    clean = [v for _, v in series if v is not None]
    if len(clean) < 2:
        return "<p class='muted sm'>No data.</p>"
    lo, hi = min(clean), max(clean)
    rng = (hi - lo) or 1
    n, w = len(clean), 320
    pts = " ".join(f"{i / (n - 1) * w:.1f},{h - (v - lo) / rng * h:.1f}"
                   for i, v in enumerate(clean))
    return (f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" class="area">'
            f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2" '
            f'stroke-linejoin="round"/></svg>')


def _hypnogram(stages, w=320, h=104):
    segs = [(s.get("stage"), s.get("duration", 0) or 0) for s in stages]
    total = sum(d for _, d in segs) or 1
    level = {"awake": 0, "out": 0, "rem": 1, "light": 2, "deep": 3}
    rowh = h / 4
    x, rects = 0.0, []
    for s, d in segs:
        wseg = d / total * w
        y = level.get(s, 2) * rowh
        rects.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(0.6, wseg):.1f}" '
                     f'height="{rowh - 1:.1f}" rx="1" fill="{STAGE_COL.get(s, "#3ba0c4")}"/>')
        x += wseg
    return (f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" class="hypno">'
            f'{"".join(rects)}</svg>')


def _hrv_band(series, mean, sd, h=96):
    clean = [(i, v) for i, (_, v) in enumerate(series) if v is not None]
    if len(clean) < 2:
        return "<p class='muted sm'>Not enough history.</p>"
    vals = [v for _, v in clean]
    lo = min(vals + ([mean - 2 * sd] if mean and sd else []))
    hi = max(vals + ([mean + 2 * sd] if mean and sd else []))
    rng = (hi - lo) or 1
    n, w = len(clean), 320

    def py(v):
        return h - (v - lo) / rng * h

    pts = " ".join(f"{i / (n - 1) * w:.1f},{py(v):.1f}" for i, v in clean)
    bandrect = ""
    if mean and sd:
        bandrect = (f'<rect x="0" y="{py(mean + sd):.1f}" width="{w}" '
                    f'height="{(py(mean - sd) - py(mean + sd)):.1f}" '
                    f'fill="var(--g)" opacity="0.12"/>'
                    f'<line x1="0" y1="{py(mean):.1f}" x2="{w}" y2="{py(mean):.1f}" '
                    f'stroke="var(--muted)" stroke-width="0.6" stroke-dasharray="3 3"/>')
    return (f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" class="area">{bandrect}'
            f'<polyline points="{pts}" fill="none" stroke="var(--g)" stroke-width="2" '
            f'stroke-linejoin="round"/></svg>')


def _spark_svg(vals):
    vals = [v for v in vals if v is not None][-60:]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1
    n, w, h = len(vals), 100, 28
    pts = " ".join(f"{i / (n - 1) * w:.1f},{h - (v - lo) / rng * h:.1f}" for i, v in enumerate(vals))
    return (f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" class="spk">'
            f'<polyline points="{pts}" fill="none" stroke="var(--teal)" stroke-width="1.5"/></svg>')


def _trend_row(label, series, unit="", dp=0, scale=1.0):
    pts = [v * scale for v in series if v is not None]
    if not pts:
        return ""
    cur, recent = pts[-1], pts[-30:]
    avg = sum(recent) / len(recent)
    return (f'<div class="trow"><div class="tlbl">{label}</div>'
            f'<div class="tspark">{_spark_svg(pts)}</div>'
            f'<div class="tval">{cur:.{dp}f}{unit}<span class="tavg">avg {avg:.{dp}f}</span></div></div>')


def _readiness_trend_svg(hist):
    hist = hist[-45:]
    if not hist:
        return ""
    n, w, h, gap = len(hist), 320, 84, 2
    bw = (w - (n - 1) * gap) / n
    bars = [f'<rect x="{i * (bw + gap):.1f}" y="{h - max(2, (sc or 0) / 100 * h):.1f}" '
            f'width="{bw:.1f}" height="{max(2, (sc or 0) / 100 * h):.1f}" rx="1.5" '
            f'fill="{band(sc)[1]}"><title>{d}: {round(sc or 0)}</title></rect>'
            for i, (d, sc) in enumerate(hist)]
    return (f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" class="trend">'
            f'{"".join(bars)}</svg>')


# -------------------------------------------------------------- small bits
def _vital(label, value, mean, sd, unit, higher_better, dp=0):
    if value is None:
        return ""
    vs = f"{value:.{dp}f}{unit}"
    if mean is None or sd is None or sd == 0:
        return (f'<div class="vital"><div class="vl">{label}</div>'
                f'<div class="vv">{vs}</div><div class="vr"></div></div>')
    lo, hi = mean - sd, mean + sd
    status = "in" if lo <= value <= hi else ("high" if value > hi else "low")
    bad = (status == "low" and higher_better) or (status == "high" and not higher_better)
    col = "var(--amber)" if (status != "in" and bad) else "var(--teal)"
    smin, smax = mean - 3 * sd, mean + 3 * sd
    span = (smax - smin) or 1

    def pos(x):
        return max(0, min(100, (x - smin) / span * 100))

    bl, br = pos(lo), pos(hi)
    bar = (f'<div class="vr"><div class="vband" style="left:{bl:.0f}%;width:{br - bl:.0f}%">'
           f'</div><div class="vdot" style="left:{pos(value):.0f}%;background:{col}"></div></div>')
    return f'<div class="vital"><div class="vl">{label}</div><div class="vv">{vs}</div>{bar}</div>'


def _stage_legend(m):
    items = [("Deep", m["deep_min"], "#4062c0"), ("REM", m["rem_min"], "#8b5cf6"),
             ("Light", m["light_min"], "#3ba0c4"), ("Awake", m["awake_min"], "#5a6472")]
    return "".join(f'<span class="lg"><i style="background:{c}"></i>{n} {round(v or 0)}m</span>'
                   for n, v, c in items)


# -------------------------------------------------------------- activities
def _hms(s):
    s = int(s or 0)
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}h {m:02d}m" if h else f"{m}m {sec:02d}s"


def _pace(spd, sport):
    if not spd or spd <= 0:
        return None
    t = (sport or "").lower()
    if t.startswith("swim"):
        p = 100 / spd
        return f"{int(p // 60)}:{int(p % 60):02d} /100m"
    if t.startswith(("run", "walk")):
        p = 1000 / spd
        return f"{int(p // 60)}:{int(p % 60):02d} /km"
    return None


def _activity_detail(p, trimp):
    rows = []

    def add(label, val):
        if val not in (None, "", 0, 0.0):
            rows.append((label, val))

    secs = p.get("moving_time") or p.get("elapsed_time")
    add("Duration", _hms(secs) if secs else None)
    dist = p.get("distance")
    add("Distance", (f"{dist / 1000:.2f} km" if dist and dist >= 1000
                     else (f"{round(dist)} m" if dist else None)))
    add("Pace", _pace(p.get("average_speed"), p.get("type")))
    add("Avg HR", f"{round(p['average_heartrate'])} bpm" if p.get("average_heartrate") else None)
    add("Max HR", f"{round(p['max_heartrate'])} bpm" if p.get("max_heartrate") else None)
    spd = p.get("average_speed")
    add("Avg speed", f"{spd * 3.6:.1f} km/h" if spd else None)
    add("Cadence", round(p["average_cadence"]) if p.get("average_cadence") else None)
    add("Calories", f"{round(p['calories'])} kcal" if p.get("calories") else None)
    add("Elevation", f"{round(p['total_elevation_gain'])} m" if p.get("total_elevation_gain") else None)
    add("Our TRIMP", round(trimp) if trimp else None)
    add("Intervals load", round(p["icu_training_load"]) if p.get("icu_training_load") else None)
    add("Device", p.get("device_name"))
    add("Source", p.get("source"))
    return rows


def _activities_html(acts):
    if not acts:
        return "<p class='muted sm'>No recent activities.</p>"
    out = []
    for st, sport, src, ahr, trimp, sup, dist, payload in acts:
        try:
            p = json.loads(payload) if payload else {}
        except (TypeError, ValueError):
            p = {}
        is_form = src and src != "GARMIN_CONNECT"
        badge, bcls = ("FORM", "b-form") if is_form else ("Garmin", "b-garmin")
        strike = " struck" if sup else ""
        hr = f"{round(ahr)} bpm" if ahr else "--"
        load = f"load {round(trimp)}" if trimp else ""
        drows = "".join(f'<div class="dk">{k}</div><div class="dv">{v}</div>'
                        for k, v in _activity_detail(p, trimp))
        out.append(f"""
        <details class="act{strike}"><summary>
          <span class="act-ic">{_icon(sport)}</span>
          <span class="act-mid"><span class="act-top">{sport or 'Activity'} <span class="badge {bcls}">{badge}</span></span>
          <span class="muted sm">{_clock(p.get('start_date_local')) or (st or '')[:10]} &middot; {hr} &middot; {load}</span></span>
          <span class="chev">&rsaquo;</span></summary>
          <div class="detail">{drows}</div></details>""")
    return "".join(out)


# -------------------------------------------------------------- insights
def _insights(m, rd):
    out = []

    def add(icon, text, tone="n"):
        out.append((icon, text, tone))

    hi = lambda v, mn, sd, k=1.5: v is not None and mn is not None and sd and v > mn + k * sd
    if (hi(m["resp"], m["resp_mean"], m["resp_sd"]) or hi(m["rhr"], m["rhr_mean"], m["rhr_sd"])):
        add("\U0001FA7A", "Illness watch: resting HR and/or respiration are above your "
                          "normal range. Ease off and watch how you feel.", "warn")
    if m["form"] is not None and m["form"] < -20:
        add("\U0001F525", f"Fatigue is high (form {round(m['form'])}). You're carrying load; "
                          "favour recovery over intensity.", "warn")
    if m["hrv_status"] == "Low":
        add("\U0001F4C9", "HRV is suppressed vs your baseline. Your body is under load.", "warn")
    if rd is not None and rd["debt_sub"] is not None and rd["debt_sub"] < 60:
        add("\U0001F634", "Sleep debt is building. An earlier night would help recovery.", "warn")
    if m["readiness"] and m["readiness"] >= 70 and (m["form"] or 0) > -10:
        add("✅", "Recovered and ready. A good day to push if you want to.", "ok")
    if not out:
        add("\U0001F44D", "Nothing flagged. Metrics are in your normal ranges.", "ok")
    return "".join(
        f'<div class="ins ins-{t}"><span class="ins-i">{i}</span><span>{txt}</span></div>'
        for i, txt, t in out[:4])


# -------------------------------------------------------------- panels
def _panel_today(m, rd, updated, brief):
    score = m["readiness"]
    label, col = band(score)
    circ = 2 * 3.14159 * 54
    dash = circ * (1 - (score or 0) / 100)
    sh = (m["sleep_min"] or 0) / 60
    quick = [
        ("Energy", round(m["energy"] or 0), ""), ("Sleep", f"{sh:.1f}", "h"),
        ("HRV", round(m["hrv"] or 0), ""), ("Strain", m["strain"] or 0, "")]
    qh = "".join(f'<div><div class="q-v">{v}{u}</div><div class="q-k">{k}</div></div>'
                 for k, v, u in quick)
    return f"""
  <section class="panel" id="tab-today">
    <div class="hd"><div class="hd-t">Today</div><div class="hd-s">{updated}</div></div>
    <div class="hero">
      <div class="ring">
        <svg width="150" height="150">
          <circle cx="75" cy="75" r="54" fill="none" stroke="var(--card2)" stroke-width="11"/>
          <circle cx="75" cy="75" r="54" fill="none" stroke="{col}" stroke-width="11"
            stroke-linecap="round" stroke-dasharray="{circ:.1f}" stroke-dashoffset="{dash:.1f}"
            transform="rotate(-90 75 75)"/></svg>
        <div class="ring-n"><b>{round(score or 0)}</b><span>READY</span></div>
      </div>
      <div class="verdict" style="color:{col}">{label}</div>
      <div class="brief">{brief}</div>
    </div>
    <div class="qgrid">{qh}</div>
    <p class="lbl">Insights</p>
    {_insights(m, rd)}
  </section>"""


def _panel_sleep(m, es):
    asleep, wake = _clock(es.get("asleep")), _clock(es.get("wake"))
    when = f"asleep {asleep}, woke {wake}" if asleep and wake else "last night"
    sh = (m["sleep_min"] or 0) / 60
    eff = f" &middot; {round(m['efficiency'])}% efficient" if m["efficiency"] else ""
    hyp = _hypnogram(es.get("stages", [])) if es.get("stages") else "<p class='muted sm'>No stage data.</p>"
    hr_c = _line(es.get("hr", []), C_HR) if es.get("hr") else ""
    hrv_c = _line(es.get("hrv", []), C_HRV) if es.get("hrv") else ""
    return f"""
  <section class="panel hidden" id="tab-sleep">
    <div class="hd"><div class="hd-t">Sleep</div><div class="hd-s">{_chip('Eight Sleep')} {when}</div></div>
    <div class="card">
      <p class="lbl">Stages &middot; {sh:.1f}h{eff}</p>
      {hyp}
      <div class="legend" style="margin-top:10px">{_stage_legend(m)}</div>
    </div>
    <div class="card"><p class="lbl">Overnight heart rate</p>{hr_c}</div>
    <div class="card"><p class="lbl">Overnight HRV</p>{hrv_c}</div>
  </section>"""


def _panel_body(m, bb, stress, gstamp):
    bb_c = _area(bb, C_EN, "bbg") if bb else "<p class='muted sm'>No Body Battery data yet.</p>"
    st_c = _area([(x, y) for x, y in stress], C_ST, "stg", ymax=100) if stress else "<p class='muted sm'>No stress data yet.</p>"
    gbb = ""
    if m["gbb_wake"] is not None or m["gbb_now"] is not None:
        gbb = f'<p class="muted sm" style="margin:8px 0 0">Garmin: {round(m["gbb_wake"] or 0)} at wake &rarr; {round(m["gbb_now"] or 0)} now</p>'
    vitals = (
        _vital("HRV", m["hrv"], m["hrv_mean"], m["hrv_sd"], " ms", True, 0)
        + _vital("Resting HR", m["rhr"], m["rhr_mean"], m["rhr_sd"], " bpm", False, 0)
        + _vital("Respiratory", m["resp"], m["resp_mean"], m["resp_sd"], " /min", False, 1)
        + _vital("Sleep", (m["sleep_min"] / 60 if m["sleep_min"] else None),
                 (m["sleep_mean"] / 60 if m["sleep_mean"] else None),
                 (m["sleep_sd"] / 60 if m["sleep_sd"] else None), " h", True, 1)
        + _vital("Stress", m["stress"], m["stress_mean"], m["stress_sd"], "", False, 0)
        + _vital("SpO2", m["spo2"], None, None, "%", True, 0))
    return f"""
  <section class="panel hidden" id="tab-body">
    <div class="hd"><div class="hd-t">Body</div><div class="hd-s">{_chip('Garmin')} {gstamp}</div></div>
    <div class="card"><p class="lbl">Body Battery &middot; energy through the day</p>{bb_c}{gbb}</div>
    <div class="card"><p class="lbl">Stress</p>{st_c}</div>
    <div class="card"><p class="lbl">Vitals vs your typical range</p>{vitals}</div>
  </section>"""


def _panel_load(m, acts):
    ts = m["training_status"] or "--"
    rec = f' &middot; ~{round(m["recovery_hours"])}h to recover' if m["recovery_hours"] else ""
    return f"""
  <section class="panel hidden" id="tab-load">
    <div class="hd"><div class="hd-t">Load</div><div class="hd-s">{_chip('Garmin')} {_chip('FORM')}</div></div>
    <div class="card">
      <div class="statline"><span class="pill">{ts}</span>
        <span class="muted sm">strain {m['strain'] or 0} / 21{rec}</span></div>
      <div class="grid3" style="margin-top:14px">
        <div><div class="k">Fitness</div><div class="v">{round(m['ctl'] or 0)}</div></div>
        <div><div class="k">Fatigue</div><div class="v">{round(m['atl'] or 0)}</div></div>
        <div><div class="k">Form</div><div class="v">{(m['form'] or 0):+.0f}</div></div>
      </div>
    </div>
    <div class="card"><p class="lbl">Recent activity</p>{_activities_html(acts)}</div>
  </section>"""


def _panel_trends(m, hist, vo2max):
    def series(key):
        return [r[key] for r in hist]
    rows = (_trend_row("Resting HR", series("rhr"), " bpm", 0)
            + _trend_row("Respiratory", series("resp"), "", 1)
            + _trend_row("Sleep", series("sleep_min"), " h", 1, scale=1 / 60)
            + _trend_row("Readiness", series("readiness"), "", 0)
            + _trend_row("Energy", series("energy"), "", 0)
            + _trend_row("Stress", series("stress"), "", 0))
    vo2 = f'{vo2max:.0f}' if vo2max else '--'
    return f"""
  <section class="panel hidden" id="tab-trends">
    <div class="hd"><div class="hd-t">Trends</div><div class="hd-s">VO2max {vo2}</div></div>
    <div class="card">
      <p class="lbl">HRV vs baseline &middot; last 45 days</p>
      {_hrv_band([(r['day'], r['hrv']) for r in hist], m['hrv_mean'], m['hrv_sd'])}
    </div>
    <div class="card"><p class="lbl">Metrics</p>{rows}</div>
    <div class="card"><p class="lbl">Readiness</p>{_readiness_trend_svg([(r['day'], r['readiness']) for r in hist])}</div>
  </section>"""


def _nav():
    tabs = [("today", "☀️", "Today"), ("sleep", "\U0001F319", "Sleep"),
            ("body", "❤️", "Body"), ("load", "\U0001F3CB️", "Load"),
            ("trends", "\U0001F4C8", "Trends")]
    return '<nav class="nav">' + "".join(
        f'<button class="navbtn{" active" if t == "today" else ""}" data-tab="{t}">'
        f'<span class="ni">{i}</span>{lbl}</button>' for t, i, lbl in tabs) + '</nav>'


def _body(m, rd, hist, acts, vo2max, es, bb, stress):
    updated = f"Updated {datetime.now(TZ).strftime('%a %d %b, %H:%M')}"
    sh = (m["sleep_min"] or 0) / 60
    strain = m["strain"] or 0
    sload = "low" if strain < 7 else "moderate" if strain < 13 else "high"
    verdict = {"Prime": "Primed to perform.", "Good": "Recovered and ready.",
               "Moderate": "Train with awareness.", "Low": "Prioritise recovery."}.get(band(m["readiness"])[0], "")
    hs = (m["hrv_status"] or "steady").lower()
    brief = f"Slept {sh:.1f}h, HRV {hs}, {sload} strain banked. {verdict}"
    gstamp = ""
    if bb:
        gstamp = "as of " + (_clock(datetime.fromtimestamp(bb[-1][0] / 1000, tz=timezone.utc).isoformat()) or "")
    return (_panel_today(m, rd, updated, brief)
            + _panel_sleep(m, es)
            + _panel_body(m, bb, stress, gstamp)
            + _panel_load(m, acts)
            + _panel_trends(m, hist, vo2max)
            + _nav())


CSS = """
  :root{--bg:#0b0e13;--card:#161b23;--card2:#1f2530;--text:#eef2f7;--muted:#8b94a3;
    --line:#262d39;--g:#37d67a;--teal:#2fc4c0;--amber:#f0b429;--red:#f0506e;--nav:#12161d;}
  @media (prefers-color-scheme: light){
    :root{--bg:#eef1f6;--card:#fff;--card2:#eef1f6;--text:#141c26;--muted:#67707e;--line:#e2e6ee;--nav:#ffffff;}}
  *{box-sizing:border-box;-webkit-tap-highlight-color:transparent;}
  body{margin:0;background:var(--bg);color:var(--text);
    font-family:-apple-system,BlinkMacSystemFont,'SF Pro Text','Segoe UI',Roboto,sans-serif;
    padding:0 0 96px;}
  .wrap{max-width:540px;margin:0 auto;padding:max(14px,env(safe-area-inset-top)) 16px 0;}
  .panel{animation:fade .25s ease;}
  .hidden{display:none;}
  @keyframes fade{from{opacity:0;transform:translateY(6px);}to{opacity:1;transform:none;}}
  .hd{display:flex;justify-content:space-between;align-items:baseline;margin:6px 0 16px;flex-wrap:wrap;gap:6px;}
  .hd-t{font-size:26px;font-weight:700;letter-spacing:-.02em;}
  .hd-s{font-size:12px;color:var(--muted);}
  .card{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:18px;margin-bottom:14px;}
  .lbl{font-size:11px;text-transform:uppercase;letter-spacing:.09em;color:var(--muted);margin:0 0 12px;font-weight:600;}
  .muted{color:var(--muted);} .sm{font-size:12px;}
  .chip{display:inline-block;font-size:10px;font-weight:600;letter-spacing:.03em;padding:3px 8px;
    border-radius:20px;background:var(--card2);color:var(--muted);margin-right:4px;}
  .hero{background:var(--card);border:1px solid var(--line);border-radius:24px;padding:26px 20px;
    text-align:center;margin-bottom:14px;}
  .ring{position:relative;width:150px;height:150px;margin:0 auto;}
  .ring-n{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;}
  .ring-n b{font-size:48px;line-height:1;letter-spacing:-.03em;}
  .ring-n span{font-size:10px;letter-spacing:.14em;color:var(--muted);margin-top:2px;}
  .verdict{font-size:22px;font-weight:700;margin-top:14px;letter-spacing:-.01em;}
  .brief{color:var(--muted);font-size:14px;line-height:1.5;margin-top:6px;max-width:340px;margin-left:auto;margin-right:auto;}
  .qgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:16px;}
  .qgrid>div{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:14px 6px;text-align:center;}
  .q-v{font-size:22px;font-weight:700;letter-spacing:-.02em;}
  .q-k{font-size:11px;color:var(--muted);margin-top:2px;}
  .ins{display:flex;gap:11px;align-items:flex-start;background:var(--card);border:1px solid var(--line);
    border-radius:16px;padding:14px;margin-bottom:10px;font-size:14px;line-height:1.45;}
  .ins-i{font-size:20px;flex:0 0 auto;}
  .ins-warn{border-color:rgba(240,180,41,.35);}
  .ins-ok{border-color:rgba(55,214,122,.3);}
  .area{width:100%;height:96px;display:block;}
  .hypno{width:100%;height:104px;display:block;}
  .spk{width:100%;height:28px;display:block;}
  .trend{width:100%;height:84px;display:block;}
  .legend{display:flex;flex-wrap:wrap;gap:12px;font-size:12px;color:var(--muted);}
  .legend .lg{display:flex;align-items:center;gap:5px;}
  .legend i{width:9px;height:9px;border-radius:2px;display:inline-block;}
  .statline{display:flex;align-items:center;gap:10px;flex-wrap:wrap;}
  .pill{font-size:12px;font-weight:700;padding:4px 12px;border-radius:20px;background:rgba(47,196,192,.16);color:var(--teal);}
  .grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;text-align:center;}
  .grid3 .k{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;}
  .grid3 .v{font-size:26px;font-weight:700;margin-top:3px;letter-spacing:-.02em;}
  .vital{display:grid;grid-template-columns:92px 66px 1fr;align-items:center;gap:10px;padding:9px 0;border-top:1px solid var(--line);}
  .vital:first-of-type{border-top:none;}
  .vl{font-size:13px;color:var(--muted);} .vv{font-size:15px;font-weight:600;text-align:right;}
  .vr{position:relative;height:8px;background:var(--card2);border-radius:4px;}
  .vband{position:absolute;top:0;bottom:0;background:rgba(47,196,192,.22);border-radius:4px;}
  .vdot{position:absolute;top:-2px;width:12px;height:12px;border-radius:50%;transform:translateX(-50%);border:2px solid var(--card);}
  .trow{display:grid;grid-template-columns:80px 1fr 92px;align-items:center;gap:10px;padding:8px 0;border-top:1px solid var(--line);}
  .trow:first-of-type{border-top:none;}
  .tlbl{font-size:13px;color:var(--muted);} .tval{font-size:14px;font-weight:600;text-align:right;}
  .tavg{display:block;font-size:11px;color:var(--muted);font-weight:400;}
  details.act{border-top:1px solid var(--line);}
  details.act:first-of-type{border-top:none;}
  details.act summary{display:flex;align-items:center;gap:12px;padding:10px 0;cursor:pointer;list-style:none;}
  details.act summary::-webkit-details-marker{display:none;}
  .act-ic{font-size:22px;flex:0 0 30px;text-align:center;}
  .act-mid{display:flex;flex-direction:column;gap:2px;} .act-top{font-size:14px;font-weight:500;}
  .struck .act-top{text-decoration:line-through;opacity:.55;}
  .badge{font-size:10px;padding:2px 6px;border-radius:6px;margin-left:6px;font-weight:600;}
  .b-form{background:rgba(47,196,192,.16);color:var(--teal);}
  .b-garmin{background:rgba(139,148,163,.16);color:var(--muted);}
  .chev{margin-left:auto;color:var(--muted);font-size:20px;transition:transform .15s;}
  details[open] .chev{transform:rotate(90deg);}
  .detail{display:grid;grid-template-columns:auto 1fr;gap:5px 14px;padding:2px 0 14px 42px;font-size:13px;}
  .dk{color:var(--muted);} .dv{text-align:right;}
  .nav{position:fixed;left:0;right:0;bottom:0;background:var(--nav);border-top:1px solid var(--line);
    display:flex;justify-content:space-around;padding:8px 4px calc(8px + env(safe-area-inset-bottom));
    backdrop-filter:blur(12px);z-index:20;}
  .navbtn{background:none;border:0;color:var(--muted);font-size:10px;font-weight:600;display:flex;
    flex-direction:column;align-items:center;gap:3px;padding:4px 10px;flex:1;font-family:inherit;}
  .navbtn .ni{font-size:20px;filter:grayscale(1);opacity:.6;}
  .navbtn.active{color:var(--teal);} .navbtn.active .ni{filter:none;opacity:1;}
  #lock{max-width:340px;margin:24vh auto 0;text-align:center;padding:0 20px;}
  #lock h3{font-weight:700;margin:0 0 4px;font-size:22px;} #lock p{color:var(--muted);font-size:13px;margin:0 0 16px;}
  #lock input{width:100%;padding:13px 14px;border-radius:12px;border:1px solid var(--line);
    background:var(--card);color:var(--text);font-size:16px;margin-bottom:10px;}
  #lock button{width:100%;padding:13px;border:0;border-radius:12px;background:var(--teal);color:#062a2a;font-weight:700;font-size:15px;}
  #lock .err{color:var(--red);font-size:13px;min-height:18px;margin-top:8px;}
"""

TAB_JS = """
(function(){
  function show(id){
    document.querySelectorAll('.panel').forEach(function(p){p.classList.toggle('hidden',p.id!=='tab-'+id);});
    document.querySelectorAll('.navbtn').forEach(function(b){b.classList.toggle('active',b.dataset.tab===id);});
    try{localStorage.setItem('tab',id);}catch(e){}
    window.scrollTo(0,0);
  }
  document.addEventListener('click',function(e){
    var b=e.target.closest?e.target.closest('.navbtn'):null; if(b){show(b.dataset.tab);}
  });
  window.__initTabs=function(){
    var t='today'; try{t=localStorage.getItem('tab')||'today';}catch(e){}
    if(!document.getElementById('tab-'+t))t='today'; show(t);
  };
})();
"""

ICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'"
        "%3E%3Crect width='100' height='100' rx='22' fill='%230b0e13'/%3E%3Ctext x='50' y='68' "
        "font-size='60' text-anchor='middle'%3E%E2%9D%A4%EF%B8%8F%3C/text%3E%3C/svg%3E")


def _head(title):
    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0b0e13">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Recovery">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<link rel="apple-touch-icon" href="{ICON}">
<title>{title}</title>
<style>{CSS}</style></head>"""


def _encrypt(plaintext, passphrase, iterations=200000):
    salt, iv = os.urandom(16), os.urandom(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                     iterations=iterations).derive(passphrase.encode())
    ct = AESGCM(key).encrypt(iv, plaintext.encode(), None)
    b = lambda x: base64.b64encode(x).decode()
    return {"salt": b(salt), "iv": b(iv), "ct": b(ct), "iter": iterations}


def _encrypted_page(body):
    blob = json.dumps(_encrypt(body, env("DASHBOARD_PASSPHRASE")))
    script = ("const BLOB=" + blob + ";\n"
              "const dec=s=>Uint8Array.from(atob(s),c=>c.charCodeAt(0));\n"
              "async function decrypt(pass){\n"
              " const km=await crypto.subtle.importKey('raw',new TextEncoder().encode(pass),'PBKDF2',false,['deriveKey']);\n"
              " const key=await crypto.subtle.deriveKey({name:'PBKDF2',salt:dec(BLOB.salt),iterations:BLOB.iter,hash:'SHA-256'},km,{name:'AES-GCM',length:256},false,['decrypt']);\n"
              " const pt=await crypto.subtle.decrypt({name:'AES-GCM',iv:dec(BLOB.iv)},key,dec(BLOB.ct));\n"
              " return new TextDecoder().decode(pt);}\n"
              "async function unlock(pass,save){try{document.getElementById('app').innerHTML=await decrypt(pass);\n"
              " document.getElementById('lock').style.display='none';\n"
              " if(save){try{localStorage.setItem('rp',pass);}catch(e){}}\n"
              " if(window.__initTabs)window.__initTabs(); return true;}catch(e){return false;}}\n"
              "document.getElementById('go').onclick=async()=>{if(!await unlock(document.getElementById('pw').value,true))document.getElementById('err').textContent='Wrong passphrase';};\n"
              "document.getElementById('pw').addEventListener('keydown',e=>{if(e.key==='Enter')document.getElementById('go').click();});\n"
              "(async()=>{let s=null;try{s=localStorage.getItem('rp');}catch(e){}if(s&&await unlock(s,false))return;})();\n"
              + TAB_JS)
    return _head("Recovery") + f"""<body>
<div class="wrap" id="app"></div>
<div id="lock"><h3>Recovery</h3><p>Enter passphrase to unlock</p>
  <input id="pw" type="password" autocomplete="current-password" autofocus>
  <button id="go">Unlock</button><div class="err" id="err"></div></div>
<script>{script}</script></body></html>"""


def _plain_page(body):
    return (_head("Recovery") + f'<body><div class="wrap">{body}</div>'
            + f'<script>{TAB_JS}window.__initTabs();</script></body></html>')


def _parse_es(raw):
    try:
        d = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    sess = d.get("sessions") or []
    s0 = max(sess, key=lambda s: len(s.get("stages") or []), default={})
    ts = s0.get("timeseries") or {}
    return {"asleep": d.get("presenceStart"), "wake": d.get("presenceEnd"),
            "stages": s0.get("stages") or [], "hr": ts.get("heartRate") or [],
            "hrv": ts.get("hrv") or [], "resp": ts.get("respiratoryRate") or []}


def _parse_arr(raw, key):
    try:
        d = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if isinstance(d, list):
        d = d[0] if d else {}
    return d.get(key) or []


def build(out_path=None, force_plain=False):
    out = Path(out_path) if out_path else (DOCS / "index.html")
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        m = con.execute("SELECT * FROM metrics_daily ORDER BY day DESC LIMIT 1").fetchone()
        rd = con.execute("SELECT * FROM readiness ORDER BY day DESC LIMIT 1").fetchone()
        hist = con.execute(
            """SELECT day, hrv, rhr, resp, sleep_min, readiness, energy, stress
               FROM metrics_daily ORDER BY day ASC""").fetchall()
        acts = con.execute(
            """SELECT a.start_time, a.sport, a.source, a.avg_hr, a.trimp, a.superseded,
                      a.distance_m, r.payload
               FROM activity a LEFT JOIN raw_pull r ON r.id = a.raw_id
               ORDER BY a.start_time DESC LIMIT 15""").fetchall()
        vrow = con.execute(
            "SELECT vo2max FROM metrics_daily WHERE vo2max IS NOT NULL ORDER BY day DESC LIMIT 1").fetchone()

        def raw(source, kind):
            r = con.execute(
                "SELECT payload FROM raw_pull WHERE source=? AND kind=? ORDER BY id DESC LIMIT 1",
                (source, kind)).fetchone()
            return r[0] if r else "{}"
        es = _parse_es(raw("eightsleep", "trend"))
        bb = _parse_arr(raw("garmin", "body_battery"), "bodyBatteryValuesArray")
        stress = _parse_arr(raw("garmin", "stress"), "stressValuesArray")
    finally:
        con.close()

    out.parent.mkdir(parents=True, exist_ok=True)
    if not m:
        out.write_text(_head("Recovery") +
            "<body><div class='wrap'><h1>Recovery</h1><p>No data yet. Run run.py.</p></div></body></html>",
            encoding="utf-8")
        return

    body = _body(m, rd, hist, acts, vrow["vo2max"] if vrow else None, es, bb, stress)
    passphrase = "" if force_plain else env("DASHBOARD_PASSPHRASE")
    if passphrase:
        page = _encrypted_page(body)
        print(f"Dashboard written (ENCRYPTED) -> {out}")
    else:
        page = _plain_page(body)
        print(f"Dashboard written (PLAINTEXT) -> {out}")
    out.write_text(page, encoding="utf-8")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 2 and sys.argv[1] == "preview":
        build(out_path=sys.argv[2], force_plain=True)
    else:
        build()
