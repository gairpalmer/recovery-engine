"""Dump the latest raw_pull payload for a source/kind to a file, for shape inspection.
Run:  .\.venv\Scripts\python.exe scripts\dump_raw.py <source> <kind> <outfile>
"""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import DB_PATH

source, kind, out = sys.argv[1], sys.argv[2], sys.argv[3]
con = sqlite3.connect(DB_PATH)
row = con.execute(
    "SELECT payload FROM raw_pull WHERE source=? AND kind=? ORDER BY id DESC LIMIT 1",
    (source, kind)).fetchone()
con.close()
payload = row[0] if row else "{}"
try:
    payload = json.dumps(json.loads(payload), indent=2)
except ValueError:
    pass
Path(out).write_text(payload, encoding="utf-8")
print(f"wrote {out} ({len(payload)} chars)")
