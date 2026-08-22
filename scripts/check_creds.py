"""Non-exposing credential sanity check: prints LENGTHS and format flags only,
never the secret values.

Run:  .\.venv\Scripts\python.exe scripts\check_creds.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import env


def check(name):
    v = env(name)
    if v is None or v == "":
        print(f"  {name}: MISSING/BLANK")
        return
    flags = []
    if v != v.strip():
        flags.append("LEADING/TRAILING SPACE")
    if v[0] in "\"'" or v[-1] in "\"'":
        flags.append("SURROUNDING QUOTE CHAR")
    if " " in v.strip():
        flags.append("contains a space")
    note = " | " + ", ".join(flags) if flags else "  (looks clean)"
    print(f"  {name}: length={len(v)}{note}")


for n in ("EIGHTSLEEP_EMAIL", "EIGHTSLEEP_PASSWORD"):
    check(n)

e = env("EIGHTSLEEP_EMAIL") or ""
print(f"  email parses as an address: {'@' in e and '.' in e.split('@')[-1]}")
