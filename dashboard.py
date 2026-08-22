"""Render a self-contained mobile dashboard to docs/index.html for GitHub Pages.

Privacy: if DASHBOARD_PASSPHRASE is set in .env, the health content is AES-256-GCM
encrypted (PBKDF2-SHA256, 200k iters) before writing. The published file holds only
ciphertext; the browser decrypts locally after the passphrase is entered once. The
passphrase never enters the repo. Without it set, the page is written in plain text
(local preview only -- do NOT publish plaintext).

Run:  .\.venv\Scripts\python.exe dashboard.py
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


def _bar(label, val):
    v = 0 if val is None else max(0, min(100, val))
    col = band(v)[1]
    return f"""
      <div class="cmp">
        <div class="cmp-l"><span>{label}</span><span>{'' if val is None else round(v)}</span></div>
        <div class="track"><div class="fill" style="width:{v}%;background:{col}"></div></div>
      </div>"""


def _trend_svg(hist):
    hist = hist[-45:]
    if not hist:
        return "<p class='muted'>No history yet.</p>"
    n = len(hist)
    w, h, gap = 320, 90, 2
    bw = (w - (n - 1) * gap) / n
    bars = []
    for i, (day, sc) in enumerate(hist):
        sc = sc or 0
        bh = max(2, sc / 100 * h)
        x, y = i * (bw + gap), h - max(2, sc / 100 * h)
        bars.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{bh:.1f}" '
                    f'rx="1.5" fill="{band(sc)[1]}"><title>{day}: {round(sc)}</title></rect>')
    return (f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" class="trend" '
            f'role="img" aria-label="readiness trend">{"".join(bars)}</svg>')


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
    """All meaningful, populated fields from the raw Intervals activity payload."""
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


def _body(r, hist, acts, form_swims):
    (day, score, hrv_s, sleep_s, rhr_s, load_s, debt_s, ctl, atl, form,
     hrv_src, sleep_src, drivers, flags) = r
    label, col = band(score)
    circ = 2 * 3.14159 * 52
    dash = circ * (1 - (score or 0) / 100)
    ctl, atl, form = ctl or 0, atl or 0, form or 0

    banners = ""
    if form_swims:
        banners += ('<div class="ban ban-ok">FORM swim data live &middot; goggle HR now '
                    'drives swim load</div>')
    if flags:
        banners += f'<div class="ban ban-warn">{flags}</div>'
    comps = (_bar("HRV", hrv_s) + _bar("Sleep", sleep_s) + _bar("Resting HR", rhr_s)
             + _bar("Load", load_s) + _bar("Sleep debt", debt_s))

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
      <div class="dt">{day}</div>
    </div>
  </div>
  <div class="card">
    <p class="lbl">Components</p>
    {comps}
    <p class="muted sm" style="margin:14px 0 0">HRV source: {hrv_src or '--'} &middot; sleep source: {sleep_src or '--'}</p>
  </div>
  <div class="card">
    <p class="lbl">Training load</p>
    <div class="grid3">
      <div><div class="k">Fitness</div><div class="v">{round(ctl)}</div></div>
      <div><div class="k">Fatigue</div><div class="v">{round(atl)}</div></div>
      <div><div class="k">Form</div><div class="v">{form:+.0f}</div></div>
    </div>
  </div>
  <div class="card">
    <p class="lbl">Readiness &middot; last 45 days</p>
    {_trend_svg(hist)}
  </div>
  <div class="card">
    <p class="lbl">Recent activity</p>
    {_activities_html(acts)}
  </div>
  <p class="foot">Sources: Eight Sleep + Garmin via Intervals.icu<br>Updated {datetime.now().strftime('%a %d %b, %H:%M')}</p>"""


CSS = """
  :root{--bg:#0e1116;--card:#171b22;--card2:#1e232c;--text:#e8ecf1;--muted:#8b94a3;
    --line:#272d38;--g:#37d67a;--teal:#2fc4c0;--amber:#f0b429;--red:#f0506e;}
  @media (prefers-color-scheme: light){
    :root{--bg:#f2f4f7;--card:#fff;--card2:#f7f9fc;--text:#16202c;--muted:#67707e;--line:#e4e8ee;}}
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
  .cmp{margin-bottom:12px;}
  .cmp-l{display:flex;justify-content:space-between;font-size:13px;margin-bottom:5px;}
  .track{height:7px;background:var(--card2);border-radius:5px;overflow:hidden;}
  .fill{height:100%;border-radius:5px;}
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
  .sm{font-size:12px;}
  .struck .act-top{text-decoration:line-through;opacity:.55;}
  .chev{margin-left:auto;color:var(--muted);font-size:20px;transition:transform .15s;}
  details[open] .chev{transform:rotate(90deg);}
  .detail{display:grid;grid-template-columns:auto 1fr;gap:5px 14px;
    padding:2px 0 14px 42px;font-size:13px;}
  .dk{color:var(--muted);}
  .dv{text-align:right;}
  .badge{font-size:10px;padding:2px 6px;border-radius:6px;margin-left:6px;vertical-align:middle;
    font-weight:600;letter-spacing:.03em;}
  .b-form{background:rgba(47,196,192,.16);color:var(--teal);}
  .b-garmin{background:rgba(139,148,163,.16);color:var(--muted);}
  .muted{color:var(--muted);}
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
    import json
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
    const html=await decrypt(pass);
    document.getElementById('app').innerHTML=html;
    document.getElementById('lock').style.display='none';
    if(save)try{{localStorage.setItem('rp',pass);}}catch(e){{}}
    return true;
  }}catch(e){{return false;}}
}}
document.getElementById('go').onclick=async()=>{{
  const v=document.getElementById('pw').value;
  if(!await unlock(v,true))document.getElementById('err').textContent='Wrong passphrase';
}};
document.getElementById('pw').addEventListener('keydown',e=>{{if(e.key==='Enter')document.getElementById('go').click();}});
(async()=>{{let s=null;try{{s=localStorage.getItem('rp');}}catch(e){{}}
  if(s&&await unlock(s,false))return;}})();
</script>
</body></html>"""


def _plain_page(body, score):
    return _head(f"Recovery {round(score)}") + f'<body><div class="wrap">{body}</div></body></html>'


def build(out_path=None, force_plain=False):
    con = sqlite3.connect(DB_PATH)
    try:
        r = con.execute(
            """SELECT day, score, hrv_sub, sleep_sub, rhr_sub, load_sub, debt_sub,
                      ctl, atl, form, hrv_source, sleep_source, drivers, flags
               FROM readiness ORDER BY day DESC LIMIT 1""").fetchone()
        hist = con.execute("SELECT day, score FROM readiness ORDER BY day ASC").fetchall()
        acts = con.execute(
            """SELECT a.start_time, a.sport, a.source, a.avg_hr, a.trimp, a.superseded,
                      a.distance_m, r.payload
               FROM activity a LEFT JOIN raw_pull r ON r.id = a.raw_id
               ORDER BY a.start_time DESC LIMIT 15""").fetchall()
        form_swims = con.execute(
            """SELECT COUNT(*) FROM activity WHERE lower(sport) LIKE 'swim%'
               AND source IS NOT NULL AND source <> 'GARMIN_CONNECT'""").fetchone()[0]
    finally:
        con.close()

    out = Path(out_path) if out_path else (DOCS / "index.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    if not r:
        out.write_text(_head("Recovery") +
            "<body><div class='wrap'><h1>Recovery</h1><p>No data yet. Run run.py.</p></div></body></html>",
            encoding="utf-8")
        return

    body = _body(r, hist, acts, form_swims)
    passphrase = "" if force_plain else env("DASHBOARD_PASSPHRASE")
    if passphrase:
        page = _encrypted_page(body)
        print(f"Dashboard written (ENCRYPTED) -> {out}")
    else:
        page = _plain_page(body, r[1])
        print(f"Dashboard written (PLAINTEXT) -> {out}")
    out.write_text(page, encoding="utf-8")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 2 and sys.argv[1] == "preview":
        build(out_path=sys.argv[2], force_plain=True)
    else:
        build()
