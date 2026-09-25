"""One-off clean-up of the local catalogue database.

- Backs up instance/datasets.db before touching it.
- Normalises casing of languages, modalities (dataset types) and formats.
- Splits combined values such as "text , audio , csv" into proper list items.
- Rewrites local folder paths relative to the datasets root so they keep
  working if the project folder moves.

Run with --dry-run first to see what would change.
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path, PureWindowsPath

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app import DATASETS_ROOT, DB_PATH, canonical_value, join_csv, split_csv  # noqa: E402

DRY_RUN = "--dry-run" in sys.argv

# Manual corrections for paths that pointed at the wrong folder.
PATH_FIXES = {
    13: "MedQA-Darija-MultiLingual",
    31: "mental_health_social_media_posts",
}
KNOWN_FORMATS = {"CSV", "JSON", "TSV", "TXT", "XML", "XLSX", "JSONL"}


def relative_dataset_path(value: str) -> str:
    if not value:
        return ""
    parts = PureWindowsPath(value).parts
    lowered = [part.lower() for part in parts]
    if "downloaded datasets" in lowered:
        index = lowered.index("downloaded datasets")
        remainder = parts[index + 1:]
        if remainder and (DATASETS_ROOT.joinpath(*remainder)).exists():
            return "/".join(remainder)
    return value


def main() -> None:
    if not DB_PATH.exists():
        print(f"No database at {DB_PATH}")
        return

    if not DRY_RUN:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = DB_PATH.with_name(f"datasets.backup-{stamp}.db")
        shutil.copy2(DB_PATH, backup)
        print(f"Backup written to {backup}")

    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    rows = connection.execute("SELECT * FROM datasets").fetchall()

    for row in rows:
        updates = {}

        languages = join_csv([canonical_value("language", v) for v in split_csv(row["languages"])])
        types = [canonical_value("dataset_type", v) for v in split_csv(row["dataset_type"])]
        types = join_csv([t for t in types if t.upper() not in KNOWN_FORMATS])
        fmt = canonical_value("format", row["format"] or "")
        path = PATH_FIXES.get(row["id"]) or relative_dataset_path(row["local_path"] or "")

        for key, value in (("languages", languages), ("dataset_type", types), ("format", fmt), ("local_path", path)):
            if (row[key] or "") != value:
                updates[key] = value

        if updates:
            print(f"#{row['id']} {row['name']}")
            for key, value in updates.items():
                print(f"    {key}: {row[key]!r} -> {value!r}")
            if not DRY_RUN:
                assignments = ", ".join(f"{key} = ?" for key in updates)
                connection.execute(
                    f"UPDATE datasets SET {assignments} WHERE id = ?",
                    [*updates.values(), row["id"]],
                )

    # Rebuild list-type taxonomy values from their canonical forms.
    taxonomy = connection.execute("SELECT id, category, value FROM taxonomy").fetchall()
    for item in taxonomy:
        values = [canonical_value(item["category"], v) for v in split_csv(item["value"])]
        if item["category"] == "dataset_type":
            values = [v for v in values if v.upper() not in KNOWN_FORMATS]
        if values == [item["value"]]:
            continue
        print(f"taxonomy {item['category']}: {item['value']!r} -> {values}")
        if not DRY_RUN:
            connection.execute("DELETE FROM taxonomy WHERE id = ?", (item["id"],))
            for value in values:
                connection.execute(
                    "INSERT OR IGNORE INTO taxonomy (category, value, created_at) VALUES (?, ?, ?)",
                    (item["category"], value, datetime.now().isoformat(timespec="seconds") + "Z"),
                )

    if not DRY_RUN:
        connection.commit()
    connection.close()
    print("Dry run only, nothing written." if DRY_RUN else "Done.")


if __name__ == "__main__":
    main()
