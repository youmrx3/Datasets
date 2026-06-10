"""Export local SQLite database + uploads for Vercel deployment."""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "instance" / "datasets.db"
OUTPUT_PATH = BASE_DIR / "seed_data.json"
UPLOADS_DIR = BASE_DIR / "uploads"
SEED_UPLOADS_DIR = BASE_DIR / "seed_uploads"

if not DB_PATH.exists():
    print(f"Database not found at {DB_PATH}")
    sys.exit(1)

connection = sqlite3.connect(DB_PATH)
connection.row_factory = sqlite3.Row

tables = ["datasets", "attachments", "taxonomy", "settings"]
data = {}

for table in tables:
    rows = connection.execute(f"SELECT * FROM {table}").fetchall()
    data[table] = [dict(row) for row in rows]

connection.close()

OUTPUT_PATH.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
print(f"Exported {sum(len(v) for v in data.values())} rows to {OUTPUT_PATH}")
print(f"  datasets:    {len(data['datasets'])}")
print(f"  attachments: {len(data['attachments'])}")
print(f"  taxonomy:    {len(data['taxonomy'])}")
print(f"  settings:    {len(data['settings'])}")

if UPLOADS_DIR.exists():
    SEED_UPLOADS_DIR.mkdir(exist_ok=True)
    count = 0
    for f in UPLOADS_DIR.iterdir():
        if f.is_file():
            shutil.copy2(f, SEED_UPLOADS_DIR / f.name)
            count += 1
    print(f"  uploads:     {count} files copied to {SEED_UPLOADS_DIR}")
else:
    print("  uploads:     0 (no uploads/ directory)")
