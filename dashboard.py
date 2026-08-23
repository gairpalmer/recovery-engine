"""Render a self-contained mobile recovery dashboard to docs/index.html.

Sections: readiness, energy (Body Battery), vitals vs typical range, HRV status,
sleep detail, training load / strain, trends, and expandable activities. Driven by
the metrics_daily + readiness + activity tables.

Privacy: if DASHBOARD_PASSPHRASE is set, the body is AES-256-GCM encrypted before
writing; the published file is ciphertext, decrypted in the browser. See README.

Run:  .\.venv\Scripts\python.exe dashboard.py            (encrypted, to docs/)
      .\.venv\Scripts\python.exe dashboard.py preview X  (plaintext preview to X)
"""
import base64
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from config import BASE, DB_PATH, env

DOCS = BASE / "docs"

SPORT_ICON = {"swim": "\U0001F3CA", "tennis": "\U0001F3BE", "golf": "⛳",
              "run": "\U0001F3C3", "ride": "\U0001F6B4", "walk": "\U0001F6B6",
              "strength": "\U0001F3CB"}


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


# ---------------------------------------------------------------- activities
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
    t = p.get("average_temp")
    add("Avg temp", f"{t:.0f}°C" if isinstance(t, (int, float)) else None)
    add("Our TRIMP", round(trimp) if trimp else None)
    add("Intervals load", round(p["icu_training_load"]) if p.get("icu_training_load") else None)
    add("Device", p.get("device_name"))
    add("Source", p.get("source"))
    started = p.get("start_date_local")
    add("Started", started.replace("T", " ") if started else None)
    return rows


def _activities_html(acts):
    if not acts:
        return "<p class='muted'>No recent activities.</p>"
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
        <details class="act{strike}">
          <summary>
            <span class="act-ic">{_icon(sport)}</span>
            <span class="act-mid">
              <span class="act-top">{sport or 'Activity'} <span class="badge {bcls}">{badge}</span></span>
              <span class="muted sm">{(st or '')[:10]} &middot; {hr} &middot; {load}</span>
            </span>
            <span class="chev">&rsaquo;</span>
          </summary>
          <div class="detail">{drows}</div>
        </details>""")
    return "".join(out)


# ---------------------------------------------------------------- components
def _battery(energy):
    e = 0 if energy is None else max(0, min(100, energy))
    return (f'<div class="batt"><div class="batt-fill" style="width:{e}%;'
            f'background:{band(e)[1]}"></div><span class="batt-num">{round(e)}</span></div>')


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


def _stages(deep, rem, light, awake):
    segs = [("Deep", deep, "#4062c0"), ("REM", rem, "#8b5cf6"),
            ("Light", light, "#3ba0c4"), ("Awake", awake, "#5a6472")]
    total = sum(v for _, v, _ in segs if v) or 1
    bars = "".join(f'<div style="width:{(v or 0) / total * 100:.1f}%;background:{c}"></div>'
                   for _, v, c in segs)
    legend = "".join(f'<span class="lg"><i style="background:{c}"></i>{n} {round(v or 0)}m</span>'
                     for n, v, c in segs)
    return f'<div class="stages">{bars}</div><div class="legend">{legend}</div>'


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
    cur = pts[-1]
    recent = pts[-30:]
    avg = sum(recent) / len(recent)
    return (f'<div class="trow"><div class="tlbl">{label}</div>'
            f'<div class="tspark">{_spark_svg(pts)}</div>'
            f'<div class="tval">{cur:.{dp}f}{unit}<span class="tavg">avg {avg:.{dp}f}</span></div></div>')


def _trend_svg(hist):
    hist = hist[-45:]
    if not hist:
        return "<p class='muted'>No history yet.</p>"
    n, w, h, gap = len(hist), 320, 90, 2
    bw = (w - (n - 1) * gap) / n
    bars = []
    for i, (day, sc) in enumerate(hist):
        sc = sc or 0
        bh = max(2, sc / 100 * h)
        bars.append(f'<rect x="{i * (bw + gap):.1f}" y="{h - bh:.1f}" width="{bw:.1f}" '
                    f'height="{bh:.1f}" rx="1.5" fill="{band(sc)[1]}"><title>{day}: {round(sc)}</title></rect>')
    return (f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" class="trend" '
            f'role="img" aria-label="readiness trend">{"".join(bars)}</svg>')


# ---------------------------------------------------------------- page body
def _body(m, rd, hist, acts, form_swims, vo2max):
    score = m["readiness"]
    label, col = band(score)
    circ = 2 * 3.14159 * 52
    dash = circ * (1 - (score or 0) / 100)
    drivers = rd["drivers"] if rd else ""
    flags = rd["flags"] if rd else ""
    hrv_src = rd["hrv_source"] if rd else "--"
    sleep_src = rd["sleep_source"] if rd else "--"

    banners = ""
    if form_swims:
        banners += ('<div class="ban ban-ok">FORM swim data live &middot; goggle HR drives swim load</div>')
    if flags:
        banners += f'<div class="ban ban-warn">{flags}</div>'

    # vitals (temperature omitted -- Eight Sleep exposes bed temp, not skin temp)
    vitals = (
        _vital("HRV", m["hrv"], m["hrv_mean"], m["hrv_sd"], " ms", True, 0)
        + _vital("Resting HR", m["rhr"], m["rhr_mean"], m["rhr_sd"], " bpm", False, 0)
        + _vital("Respiratory", m["resp"], m["resp_mean"], m["resp_sd"], " /min", False, 1)
        + _vital("Sleep", (m["sleep_min"] / 60 if m["sleep_min"] else None),
                 (m["sleep_mean"] / 60 if m["sleep_mean"] else None),
                 (m["sleep_sd"] / 60 if m["sleep_sd"] else None), " h", True, 1)
        + _vital("Stress", m["stress"], m["stress_mean"], m["stress_sd"], "", False, 0)
        + _vital("SpO2", m["spo2"], None, None, "%", True, 0))

    # hrv status
    hs = m["hrv_status"] or "--"
    hs_col = {"Balanced": "var(--g)", "High": "var(--teal)",
              "Low": "var(--amber)"}.get(hs, "var(--muted)")
    hrv_status_bar = _vital("7-day HRV", m["hrv_7d"], m["hrv_mean"], m["hrv_sd"], " ms", True, 0)

    # sleep
    stages = _stages(m["deep_min"], m["rem_min"], m["light_min"], m["awake_min"])
    sh = (m["sleep_min"] or 0) / 60
    eff = m["efficiency"]

    # training
    ts = m["training_status"] or "--"
    strain = m["strain"] or 0
    rec = m["recovery_hours"]

    # trends
    def series(key):
        return [r[key] for r in hist]
    trends = (
        _trend_row("HRV", series("hrv"), " ms", 0)
        + _trend_row("Resting HR", series("rhr"), " bpm", 0)
        + _trend_row("Respiratory", series("resp"), "", 1)
        + _trend_row("Sleep", series("sleep_min"), " h", 1, scale=1 / 60)
        + _trend_row("Readiness", series("readiness"), "", 0)
        + _trend_row("Energy", series("energy"), "", 0)
        + _trend_row("Stress", series("stress"), "", 0))
    vo2 = f'{vo2max:.0f}' if vo2max else '--'
    gbb_line = ""
    if m["gbb_wake"] is not None or m["gbb_now"] is not None:
        gbb_line = ('<p class="muted sm" style="margin:6px 0 0">Garmin Body Battery: '
                    f'{round(m["gbb_wake"] or 0)} at wake &rarr; {round(m["gbb_now"] or 0)} now</p>')

    return f"""
  <h1>Recovery</h1>
  {banners}
  <div class="card hero">
    <div class="ring">
      <svg width="128" height="128">
        <circle cx="64" cy="64" r="52" fill="none" stroke="var(--card2)" stroke-width="10"/>
        <circle cx="64" cy="64" r="52" fill="none" stroke="{col}" stroke-width="10"
          stroke-linecap="round" stroke-dasharray="{circ:.1f}" stroke-dashoffset="{dash:.1f}"/>
      </svg>
      <div class="num"><b>{round(score)}</b><span>{label}</span></div>
    </div>
    <div class="hero-r">
      <h2 style="color:{col}">{label}</h2>
      <div class="drv">{drivers or ''}</div>
      <div class="dt">{m['day']}</div>
    </div>
  </div>

  <div class="card">
    <p class="lbl">Energy</p>
    {_battery(m['energy'])}
    <p class="muted sm" style="margin:10px 0 0">Woke at {round(m['energy_am'] or 0)},
      now {round(m['energy'] or 0)} after today's load. Charges from sleep &amp; HRV, drains with training.</p>
    {gbb_line}
  </div>

  <div class="card">
    <p class="lbl">Vitals &middot; last night vs your typical range</p>
    {vitals}
  </div>

  <div class="card">
    <p class="lbl">HRV status</p>
    <div class="statline"><span class="pill" style="background:{hs_col}22;color:{hs_col}">{hs}</span>
      <span class="muted sm">7-day avg {round(m['hrv_7d'] or 0)} ms vs baseline {round(m['hrv_mean'] or 0)} ms</span></div>
    {hrv_status_bar}
  </div>

  <div class="card">
    <p class="lbl">Sleep &middot; {sh:.1f}h{f' &middot; {round(eff)}% efficient' if eff else ''}</p>
    {stages}
  </div>

  <div class="card">
    <p class="lbl">Training load</p>
    <div class="statline">
      <span class="pill" style="background:var(--teal)22;color:var(--teal)">{ts}</span>
      <span class="muted sm">strain {strain} / 21{f' &middot; ~{round(rec)}h to recover' if rec else ''}</span>
    </div>
    <div class="grid3" style="margin-top:14px">
      <div><div class="k">Fitness</div><div class="v">{round(m['ctl'] or 0)}</div></div>
      <div><div class="k">Fatigue</div><div class="v">{round(m['atl'] or 0)}</div></div>
      <div><div class="k">Form</div><div class="v">{(m['form'] or 0):+.0f}</div></div>
    </div>
  </div>

  <div class="card">
    <p class="lbl">Trends &middot; up to 60 days &middot; VO2max {vo2}</p>
    {trends}
  </div>

  <div class="card">
    <p class="lbl">Readiness &middot; last 45 days</p>
    {_trend_svg([(r['day'], r['readiness']) for r in hist])}
  </div>

  <div class="card">
    <p class="lbl">Recent activity</p>
    {_activities_html(acts)}
  </div>

  <p class="foot">HRV/sleep source: {hrv_src} / {sleep_src}<br>
    Eight Sleep + Garmin via Intervals.icu &middot; Updated {datetime.now().strftime('%a %d %b, %H:%M')}</p>"""


CSS = """
  :root{--bg:#0e1116;--card:#171b22;--card2:#1e232c;--text:#e8ecf1;--muted:#8b94a3;
    --line:#272d38;--g:#37d67a;--teal:#2fc4c0;--amber:#f0b429;--red:#f0506e;}
  @media (prefers-color-scheme: light){
    :root{--bg:#f2f4f7;--card:#fff;--card2:#eef1f6;--text:#16202c;--muted:#67707e;--line:#e4e8ee;}}
  *{box-sizing:border-box;-webkit-tap-highlight-color:transparent;}
  body{margin:0;background:var(--bg);color:var(--text);
    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
    padding:max(16px,env(safe-area-inset-top)) 16px calc(32px + env(safe-area-inset-bottom));}
  .wrap{max-width:520px;margin:0 auto;}
  h1{font-size:15px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);
    font-weight:600;margin:4px 0 16px;}
  .card{background:var(--card);border:1px solid var(--line);border-radius:18px;
    padding:20px;margin-bottom:14px;}
  .hero{display:flex;align-items:center;gap:20px;}
  .ring{position:relative;flex:0 0 128px;height:128px;}
  .ring svg{transform:rotate(-90deg);}
  .ring .num{position:absolute;inset:0;display:flex;flex-direction:column;
    align-items:center;justify-content:center;}
  .ring .num b{font-size:38px;line-height:1;}
  .ring .num span{font-size:11px;color:var(--muted);}
  .hero-r h2{margin:0 0 4px;font-size:22px;}
  .hero-r .drv{color:var(--muted);font-size:14px;line-height:1.4;}
  .hero-r .dt{color:var(--muted);font-size:12px;margin-top:6px;}
  .ban{border-radius:12px;padding:10px 13px;margin-bottom:10px;font-size:13px;font-weight:500;}
  .ban-ok{background:rgba(55,214,122,.13);color:var(--g);border:1px solid rgba(55,214,122,.3);}
  .ban-warn{background:rgba(240,180,41,.13);color:var(--amber);border:1px solid rgba(240,180,41,.3);}
  .lbl{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);
    margin:0 0 14px;font-weight:600;}
  .muted{color:var(--muted);}
  .sm{font-size:12px;}
  .statline{display:flex;align-items:center;gap:10px;flex-wrap:wrap;}
  .pill{font-size:12px;font-weight:700;padding:4px 11px;border-radius:20px;}
  .batt{position:relative;height:46px;background:var(--card2);border-radius:12px;overflow:hidden;}
  .batt-fill{position:absolute;left:0;top:0;bottom:0;border-radius:12px;opacity:.4;}
  .batt-num{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
    font-size:24px;font-weight:700;}
  .vital{display:grid;grid-template-columns:92px 66px 1fr;align-items:center;gap:10px;
    padding:9px 0;border-top:1px solid var(--line);}
  .vital:first-of-type{border-top:none;}
  .vl{font-size:13px;color:var(--muted);}
  .vv{font-size:15px;font-weight:600;text-align:right;}
  .vr{position:relative;height:8px;background:var(--card2);border-radius:4px;}
  .vband{position:absolute;top:0;bottom:0;background:rgba(47,196,192,.22);border-radius:4px;}
  .vdot{position:absolute;top:-2px;width:12px;height:12px;border-radius:50%;
    transform:translateX(-50%);border:2px solid var(--card);}
  .stages{display:flex;height:16px;border-radius:6px;overflow:hidden;margin-bottom:10px;}
  .stages>div{height:100%;}
  .legend{display:flex;flex-wrap:wrap;gap:12px;font-size:12px;color:var(--muted);}
  .legend .lg{display:flex;align-items:center;gap:5px;}
  .legend i{width:9px;height:9px;border-radius:2px;display:inline-block;}
  .trow{display:grid;grid-template-columns:80px 1fr 92px;align-items:center;gap:10px;
    padding:8px 0;border-top:1px solid var(--line);}
  .trow:first-of-type{border-top:none;}
  .tlbl{font-size:13px;color:var(--muted);}
  .spk{width:100%;height:28px;display:block;}
  .tval{font-size:14px;font-weight:600;text-align:right;}
  .tavg{display:block;font-size:11px;color:var(--muted);font-weight:400;}
  .grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;text-align:center;}
  .grid3 .k{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;}
  .grid3 .v{font-size:24px;font-weight:600;margin-top:3px;}
  .trend{width:100%;height:90px;display:block;}
  details.act{border-top:1px solid var(--line);}
  details.act:first-of-type{border-top:none;}
  details.act summary{display:flex;align-items:center;gap:12px;padding:10px 0;cursor:pointer;
    list-style:none;}
  details.act summary::-webkit-details-marker{display:none;}
  .act-ic{font-size:22px;flex:0 0 30px;text-align:center;}
  .act-mid{display:flex;flex-direction:column;gap:2px;}
  .act-top{font-size:14px;font-weight:500;}
  .struck .act-top{text-decoration:line-through;opacity:.55;}
  .badge{font-size:10px;padding:2px 6px;border-radius:6px;margin-left:6px;vertical-align:middle;
    font-weight:600;letter-spacing:.03em;}
  .b-form{background:rgba(47,196,192,.16);color:var(--teal);}
  .b-garmin{background:rgba(139,148,163,.16);color:var(--muted);}
  .chev{margin-left:auto;color:var(--muted);font-size:20px;transition:transform .15s;}
  details[open] .chev{transform:rotate(90deg);}
  .detail{display:grid;grid-template-columns:auto 1fr;gap:5px 14px;
    padding:2px 0 14px 42px;font-size:13px;}
  .dk{color:var(--muted);}
  .dv{text-align:right;}
  .foot{color:var(--muted);font-size:11px;text-align:center;margin-top:20px;line-height:1.6;}
  #lock{max-width:340px;margin:22vh auto 0;text-align:center;padding:0 20px;}
  #lock h3{font-weight:600;margin:0 0 4px;}
  #lock p{color:var(--muted);font-size:13px;margin:0 0 16px;}
  #lock input{width:100%;padding:12px 14px;border-radius:12px;border:1px solid var(--line);
    background:var(--card);color:var(--text);font-size:16px;margin-bottom:10px;}
  #lock button{width:100%;padding:12px;border:0;border-radius:12px;background:var(--teal);
    color:#062a2a;font-weight:700;font-size:15px;}
  #lock .err{color:var(--red);font-size:13px;min-height:18px;margin-top:8px;}
"""

ICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'"
        "%3E%3Crect width='100' height='100' rx='22' fill='%230e1116'/%3E%3Ctext x='50' y='68' "
        "font-size='60' text-anchor='middle'%3E%E2%9D%A4%EF%B8%8F%3C/text%3E%3C/svg%3E")


def _head(title):
    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0e1116">
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
    return _head("Recovery") + f"""<body>
<div class="wrap" id="app"></div>
<div id="lock">
  <h3>Recovery</h3><p>Enter passphrase to unlock</p>
  <input id="pw" type="password" autocomplete="current-password" autofocus>
  <button id="go">Unlock</button>
  <div class="err" id="err"></div>
</div>
<script>
const BLOB={blob};
const dec=s=>Uint8Array.from(atob(s),c=>c.charCodeAt(0));
async function decrypt(pass){{
  const km=await crypto.subtle.importKey('raw',new TextEncoder().encode(pass),'PBKDF2',false,['deriveKey']);
  const key=await crypto.subtle.deriveKey({{name:'PBKDF2',salt:dec(BLOB.salt),iterations:BLOB.iter,hash:'SHA-256'}},
    km,{{name:'AES-GCM',length:256}},false,['decrypt']);
  const pt=await crypto.subtle.decrypt({{name:'AES-GCM',iv:dec(BLOB.iv)}},key,dec(BLOB.ct));
  return new TextDecoder().decode(pt);
}}
async function unlock(pass,save){{
  try{{
    document.getElementById('app').innerHTML=await decrypt(pass);
    document.getElementById('lock').style.display='none';
    if(save)try{{localStorage.setItem('rp',pass);}}catch(e){{}}
    return true;
  }}catch(e){{return false;}}
}}
document.getElementById('go').onclick=async()=>{{
  if(!await unlock(document.getElementById('pw').value,true))
    document.getElementById('err').textContent='Wrong passphrase';
}};
document.getElementById('pw').addEventListener('keydown',e=>{{if(e.key==='Enter')document.getElementById('go').click();}});
(async()=>{{let s=null;try{{s=localStorage.getItem('rp');}}catch(e){{}}
  if(s&&await unlock(s,false))return;}})();
</script>
</body></html>"""


def _plain_page(body):
    return _head("Recovery") + f'<body><div class="wrap">{body}</div></body></html>'


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
        form_swims = con.execute(
            """SELECT COUNT(*) FROM activity WHERE lower(sport) LIKE 'swim%'
               AND source IS NOT NULL AND source <> 'GARMIN_CONNECT'""").fetchone()[0]
        vrow = con.execute(
            "SELECT vo2max FROM metrics_daily WHERE vo2max IS NOT NULL ORDER BY day DESC LIMIT 1").fetchone()
    finally:
        con.close()

    out.parent.mkdir(parents=True, exist_ok=True)
    if not m:
        out.write_text(_head("Recovery") +
            "<body><div class='wrap'><h1>Recovery</h1><p>No data yet. Run run.py.</p></div></body></html>",
            encoding="utf-8")
        return

    body = _body(m, rd, hist, acts, form_swims, vrow["vo2max"] if vrow else None)
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
