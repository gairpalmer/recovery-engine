"""Create the SQLite DB from schema.sql and seed default settings. Idempotent."""
import sqlite3
from config import BASE, DATA_DIR, DB_PATH, DEFAULT_SETTINGS


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    schema = (BASE / "schema.sql").read_text(encoding="utf-8")
    con = sqlite3.connect(DB_PATH)
    try:
        con.executescript(schema)
        for k, v in DEFAULT_SETTINGS.items():
            con.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v)
            )
        con.commit()
    finally:
        con.close()
    print(f"Initialised {DB_PATH}")


if __name__ == "__main__":
    main()
