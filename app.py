from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

ON_VERCEL = os.environ.get("VERCEL") == "1" or os.environ.get("VERCEL_ENV") is not None

BASE_DIR = Path(__file__).resolve().parent
if ON_VERCEL:
    INSTANCE_DIR = Path("/tmp/instance")
    UPLOAD_DIR = Path("/tmp/uploads")
else:
    INSTANCE_DIR = BASE_DIR / "instance"
    UPLOAD_DIR = BASE_DIR / "uploads"
DB_PATH = INSTANCE_DIR / "datasets.db"
SEED_DATA_FILE = BASE_DIR / "seed_data.json"
SEED_UPLOADS_DIR = BASE_DIR / "seed_uploads"
# Folder that holds the downloaded dataset files. Relative local paths are resolved against it.
DATASETS_ROOT = Path(os.environ.get("DATASETS_ROOT", BASE_DIR / "downloaded datasets")).expanduser()
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
TAXONOMY_CATEGORIES = {
    "domain": "Domains",
    "language": "Languages",
    "dataset_type": "Modalities",
    "format": "Formats",
}
# Dataset columns that hold a comma-separated list rather than a single value.
LIST_COLUMNS = {"language": "languages", "dataset_type": "dataset_type"}
SINGLE_COLUMNS = {"domain": "domain", "format": "format"}
DEFAULT_FORMATS = ["CSV", "JSON", "JSONL", "TSV", "Parquet", "XLSX", "TXT", "XML"]
EXPORT_FIELDS = [
    "id", "name", "description", "domain", "languages", "source", "dataset_type", "size",
    "samples", "format", "license", "notes", "tags", "local_path", "favorite", "created_at", "updated_at",
]
# Spectrum chart axis: log scale from 10 to 1M samples.
SPECTRUM_MIN, SPECTRUM_MAX = 10, 1_000_000
# Languages get a fixed colour slot in the order they were first added; later ones share a neutral colour.
LANGUAGE_SLOTS = 4
# Folders can only be opened when the app runs on the same machine as the files.
CAN_OPEN_FOLDERS = not ON_VERCEL

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dataset-catalog-secret")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024


# ---------------------------------------------------------------------------
# Database setup
# ---------------------------------------------------------------------------

def get_db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db() -> None:
    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    with get_db() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS datasets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                domain TEXT,
                languages TEXT,
                source TEXT,
                dataset_type TEXT,
                size TEXT,
                format TEXT,
                license TEXT,
                notes TEXT,
                tags TEXT,
                local_path TEXT,
                favorite INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dataset_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                original_filename TEXT NOT NULL,
                mime_type TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(dataset_id) REFERENCES datasets(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS taxonomy (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                value TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(category, value COLLATE NOCASE)
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        columns = {row[1] for row in connection.execute("PRAGMA table_info(datasets)").fetchall()}
        if "local_path" not in columns:
            connection.execute("ALTER TABLE datasets ADD COLUMN local_path TEXT")


def get_setting(connection: sqlite3.Connection, key: str) -> str | None:
    row = connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def seed_from_file() -> None:
    """Load seed_data.json into an empty database (used for fresh Vercel instances)."""
    if not SEED_DATA_FILE.exists():
        return
    with get_db() as connection:
        if connection.execute("SELECT COUNT(*) FROM datasets").fetchone()[0] > 0:
            return
        data = json.loads(SEED_DATA_FILE.read_text(encoding="utf-8"))
        timestamp = now_iso()
        for row in data.get("settings", []):
            connection.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (row["key"], row["value"]))
        for row in data.get("taxonomy", []):
            connection.execute(
                "INSERT OR IGNORE INTO taxonomy (category, value, created_at) VALUES (?, ?, ?)",
                (row["category"], row["value"], row.get("created_at", timestamp)),
            )
        for row in data.get("datasets", []):
            connection.execute(
                """
                INSERT OR IGNORE INTO datasets (id, name, description, domain, languages, source, dataset_type,
                    size, format, license, notes, tags, local_path, favorite, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"], row["name"], row.get("description"), row.get("domain"), row.get("languages"),
                    row.get("source"), row.get("dataset_type"), row.get("size"), row.get("format"),
                    row.get("license"), row.get("notes"), row.get("tags"), row.get("local_path"),
                    row.get("favorite", 0), row.get("created_at", timestamp), row.get("updated_at", timestamp),
                ),
            )
        for row in data.get("attachments", []):
            connection.execute(
                """
                INSERT OR IGNORE INTO attachments (id, dataset_id, filename, original_filename, mime_type, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (row["id"], row["dataset_id"], row["filename"], row["original_filename"],
                 row.get("mime_type"), row.get("created_at", timestamp)),
            )

    if SEED_UPLOADS_DIR.exists():
        for file in SEED_UPLOADS_DIR.iterdir():
            destination = UPLOAD_DIR / file.name
            if file.is_file() and not destination.exists():
                shutil.copy2(file, destination)


def seed_default_formats() -> None:
    with get_db() as connection:
        if get_setting(connection, "format_seeded") == "1":
            return
        timestamp = now_iso()
        for fmt in DEFAULT_FORMATS:
            insert_taxonomy(connection, "format", fmt, timestamp)
        set_setting(connection, "format_seeded", "1")


def sync_taxonomy_from_datasets() -> None:
    """Make sure every value used by a dataset is also available as an option."""
    with get_db() as connection:
        timestamp = now_iso()
        for dataset in connection.execute("SELECT domain, dataset_type, languages, format FROM datasets"):
            for category, column in SINGLE_COLUMNS.items():
                insert_taxonomy(connection, category, dataset[column] or "", timestamp)
            for category, column in LIST_COLUMNS.items():
                for value in split_csv(dataset[column]):
                    insert_taxonomy(connection, category, value, timestamp)


def bootstrap() -> None:
    init_db()
    seed_from_file()
    seed_default_formats()
    sync_taxonomy_from_datasets()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def join_csv(values: list[str]) -> str:
    seen: list[str] = []
    for value in values:
        cleaned = value.strip()
        if cleaned and cleaned.lower() not in {item.lower() for item in seen}:
            seen.append(cleaned)
    return ", ".join(seen)


def canonical_value(category: str, value: str) -> str:
    """Normalise casing so 'english' and 'English' don't become two options."""
    cleaned = re.sub(r"\s+", " ", value or "").strip()
    if not cleaned:
        return ""
    if category == "format":
        if cleaned.isalnum() and len(cleaned) <= 5:
            return cleaned.upper()
        return cleaned[0].upper() + cleaned[1:] if cleaned.islower() else cleaned
    if cleaned.islower():
        return cleaned[0].upper() + cleaned[1:]
    return cleaned


def parse_sample_count(size: str | None) -> int | None:
    """Pull the sample count out of free text like '35,648 consultations / 206 MB'."""
    if not size:
        return None
    head = size.split("/")[0]
    match = re.search(r"(\d[\d,.\s]*\d|\d)\s*([kKmM])?\b", head)
    if not match:
        return None
    raw, suffix = match.group(1).replace(" ", ""), (match.group(2) or "").lower()
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", raw):
        number = float(re.sub(r"[.,]", "", raw))
    else:
        try:
            number = float(raw.replace(",", ""))
        except ValueError:
            return None
    number *= {"k": 1_000, "m": 1_000_000}.get(suffix, 1)
    return int(number)


def resolve_local_path(value: str | None) -> Path | None:
    """Resolve a saved folder path, tolerating the project folder having moved."""
    if not value:
        return None
    try:
        path = Path(value).expanduser()
        if not path.is_absolute():
            return (DATASETS_ROOT / path).resolve()
        if path.exists():
            return path
        # Older entries stored absolute paths; re-root them under DATASETS_ROOT if possible.
        parts = [part.lower() for part in path.parts]
        if DATASETS_ROOT.name.lower() in parts:
            index = parts.index(DATASETS_ROOT.name.lower())
            candidate = DATASETS_ROOT.joinpath(*path.parts[index + 1:])
            if candidate.exists():
                return candidate
        return path
    except (OSError, ValueError):
        return None


def safe_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value.strip())
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return value.strip()
    return None


def allowed_image(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def dataset_from_row(row: sqlite3.Row, include_attachments: bool = False) -> dict:
    dataset = dict(row)
    dataset["favorite"] = bool(dataset["favorite"])
    dataset["languages_list"] = split_csv(dataset.get("languages"))
    dataset["types_list"] = split_csv(dataset.get("dataset_type"))
    dataset["tags_list"] = split_csv(dataset.get("tags"))
    dataset["samples"] = parse_sample_count(dataset.get("size"))
    dataset["source_url"] = safe_url(dataset.get("source"))
    dataset["url"] = url_for("dataset_detail", dataset_id=dataset["id"])
    dataset["edit_url"] = url_for("edit_dataset", dataset_id=dataset["id"])
    dataset["has_folder"] = bool(dataset.get("local_path"))
    if include_attachments:
        dataset["attachments"] = get_attachments(dataset["id"])
        folder = resolve_local_path(dataset.get("local_path"))
        dataset["folder_exists"] = bool(folder and folder.exists())
        dataset["folder_resolved"] = str(folder) if folder else ""
    return dataset


def insert_taxonomy(connection: sqlite3.Connection, category: str, value: str, timestamp: str) -> None:
    cleaned = canonical_value(category, value)
    if cleaned:
        connection.execute(
            "INSERT OR IGNORE INTO taxonomy (category, value, created_at) VALUES (?, ?, ?)",
            (category, cleaned, timestamp),
        )


def get_taxonomy_values(category: str) -> list[str]:
    with get_db() as connection:
        rows = connection.execute(
            "SELECT value FROM taxonomy WHERE category = ? ORDER BY value COLLATE NOCASE",
            (category,),
        ).fetchall()
    return [row["value"] for row in rows]


def get_taxonomy_options() -> dict[str, list[str]]:
    return {
        "domains": get_taxonomy_values("domain"),
        "dataset_types": get_taxonomy_values("dataset_type"),
        "languages": get_taxonomy_values("language"),
        "formats": get_taxonomy_values("format"),
    }


def list_match_clause(column: str) -> str:
    """SQL that matches one item inside a comma-separated column, case-insensitively."""
    return f"(',' || REPLACE(LOWER(COALESCE({column}, '')), ', ', ',') || ',') LIKE '%,' || LOWER(?) || ',%'"


def taxonomy_usage(category: str, value: str) -> int:
    with get_db() as connection:
        if category in SINGLE_COLUMNS:
            column = SINGLE_COLUMNS[category]
            query = f"SELECT COUNT(*) FROM datasets WHERE LOWER(COALESCE({column}, '')) = LOWER(?)"
        else:
            query = f"SELECT COUNT(*) FROM datasets WHERE {list_match_clause(LIST_COLUMNS[category])}"
        return connection.execute(query, (value,)).fetchone()[0]


def rename_taxonomy_in_datasets(connection: sqlite3.Connection, category: str, old: str, new: str) -> None:
    if category in SINGLE_COLUMNS:
        column = SINGLE_COLUMNS[category]
        connection.execute(
            f"UPDATE datasets SET {column} = ? WHERE LOWER(COALESCE({column}, '')) = LOWER(?)",
            (new, old),
        )
        return
    column = LIST_COLUMNS[category]
    rows = connection.execute(f"SELECT id, {column} FROM datasets WHERE {list_match_clause(column)}", (old,))
    for row in rows.fetchall():
        values = [new if item.lower() == old.lower() else item for item in split_csv(row[column])]
        connection.execute(f"UPDATE datasets SET {column} = ? WHERE id = ?", (join_csv(values), row["id"]))


def get_attachments(dataset_id: int) -> list[dict]:
    with get_db() as connection:
        rows = connection.execute(
            """
            SELECT id, dataset_id, filename, original_filename, mime_type, created_at
            FROM attachments WHERE dataset_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (dataset_id,),
        ).fetchall()
    attachments = []
    for row in rows:
        attachment = dict(row)
        attachment["url"] = url_for("uploaded_file", filename=attachment["filename"])
        attachments.append(attachment)
    return attachments


def get_thumbnails() -> dict[int, str]:
    """First attachment of every dataset, used as card thumbnails."""
    with get_db() as connection:
        rows = connection.execute(
            """
            SELECT dataset_id, filename FROM attachments a
            WHERE id = (SELECT MIN(id) FROM attachments b WHERE b.dataset_id = a.dataset_id)
            """
        ).fetchall()
    return {row["dataset_id"]: url_for("uploaded_file", filename=row["filename"]) for row in rows}


def delete_attachments(dataset_id: int, attachment_ids: list[int]) -> None:
    if not attachment_ids:
        return
    placeholders = ",".join("?" * len(attachment_ids))
    with get_db() as connection:
        rows = connection.execute(
            f"SELECT id, filename FROM attachments WHERE dataset_id = ? AND id IN ({placeholders})",
            [dataset_id, *attachment_ids],
        ).fetchall()
        for row in rows:
            (UPLOAD_DIR / row["filename"]).unlink(missing_ok=True)
        connection.execute(
            f"DELETE FROM attachments WHERE dataset_id = ? AND id IN ({placeholders})",
            [dataset_id, *attachment_ids],
        )


def get_language_slots() -> dict[str, int]:
    with get_db() as connection:
        rows = connection.execute(
            "SELECT value FROM taxonomy WHERE category = 'language' ORDER BY id LIMIT ?",
            (LANGUAGE_SLOTS,),
        ).fetchall()
    return {row["value"].lower(): index + 1 for index, row in enumerate(rows)}


def spectrum_height(samples: int | None) -> float:
    if not samples:
        return 0
    span = math.log10(SPECTRUM_MAX) - math.log10(SPECTRUM_MIN)
    value = (math.log10(max(samples, SPECTRUM_MIN)) - math.log10(SPECTRUM_MIN)) / span
    return round(min(value, 1) * 100, 2)


def get_collection_overview() -> dict:
    with get_db() as connection:
        rows = connection.execute("SELECT id, name, languages, dataset_type, size, favorite, domain FROM datasets").fetchall()
    languages: dict[str, int] = {}
    spectrum = []
    for row in rows:
        langs = split_csv(row["languages"])
        for language in langs:
            languages[language] = languages.get(language, 0) + 1
        samples = parse_sample_count(row["size"])
        spectrum.append({
            "id": row["id"],
            "name": row["name"],
            "samples": samples,
            "height": spectrum_height(samples),
            "language": langs[0] if langs else "",
            "languages": langs,
            "url": url_for("dataset_detail", dataset_id=row["id"]),
        })
    spectrum.sort(key=lambda item: item["samples"] or 0, reverse=True)
    return {
        "total": len(rows),
        "favorites": sum(1 for row in rows if row["favorite"]),
        "domains": len({row["domain"].lower() for row in rows if row["domain"]}),
        "samples": sum(item["samples"] or 0 for item in spectrum),
        "unsized": sum(1 for item in spectrum if not item["samples"]),
        "no_language": sum(1 for item in spectrum if not item["languages"]),
        "languages": sorted(languages.items(), key=lambda item: (-item[1], item[0].lower())),
        "spectrum": spectrum,
        "ticks": [
            {"label": label, "height": spectrum_height(value)}
            for label, value in (("100", 100), ("1k", 1_000), ("10k", 10_000), ("100k", 100_000), ("1M", 1_000_000))
        ],
    }


def fetch_dataset(dataset_id: int) -> dict | None:
    with get_db() as connection:
        row = connection.execute("SELECT * FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
    return dataset_from_row(row, include_attachments=True) if row else None


def query_datasets() -> list[dict]:
    args = request.args
    search = args.get("q", "").strip()
    clauses: list[str] = []
    params: list[str] = []

    if search:
        term = f"%{search.lower()}%"
        fields = ["name", "description", "notes", "tags", "source", "languages", "domain", "dataset_type", "format", "license"]
        clauses.append("(" + " OR ".join(f"LOWER(COALESCE({field}, '')) LIKE ?" for field in fields) + ")")
        params.extend([term] * len(fields))
    for arg, column in (("domain", "domain"), ("format", "format")):
        if args.get(arg, "").strip():
            clauses.append(f"LOWER(COALESCE({column}, '')) = LOWER(?)")
            params.append(args[arg].strip())
    for arg, column in (("language", "languages"), ("dataset_type", "dataset_type")):
        if args.get(arg, "").strip():
            clauses.append(list_match_clause(column))
            params.append(args[arg].strip())
    if args.get("favorite") == "1":
        clauses.append("favorite = 1")

    sort = args.get("sort", "created_desc")
    order_by = {
        "created_desc": "created_at DESC, id DESC",
        "created_asc": "created_at ASC, id ASC",
        "updated_desc": "updated_at DESC, id DESC",
        "name_asc": "LOWER(name) ASC, id ASC",
    }.get(sort, "created_at DESC, id DESC")

    query = "SELECT * FROM datasets"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += f" ORDER BY {order_by}"

    with get_db() as connection:
        rows = connection.execute(query, params).fetchall()

    thumbnails = get_thumbnails()
    datasets = []
    for row in rows:
        dataset = dataset_from_row(row)
        dataset["thumbnail"] = thumbnails.get(dataset["id"])
        datasets.append(dataset)
    if sort in {"samples_desc", "samples_asc"}:
        present = [d for d in datasets if d["samples"] is not None]
        missing = [d for d in datasets if d["samples"] is None]
        present.sort(key=lambda d: d["samples"], reverse=sort == "samples_desc")
        datasets = present + missing
    return datasets


def save_attachments(dataset_id: int) -> int:
    """Save uploaded images and return how many files were skipped."""
    skipped = 0
    timestamp = now_iso()
    with get_db() as connection:
        for uploaded_file in request.files.getlist("screenshots"):
            if not uploaded_file or not uploaded_file.filename:
                continue
            if not allowed_image(uploaded_file.filename):
                skipped += 1
                continue
            original_name = uploaded_file.filename
            filename = f"{dataset_id}_{uuid4().hex}_{secure_filename(original_name) or 'image.png'}"
            uploaded_file.save(UPLOAD_DIR / filename)
            connection.execute(
                """
                INSERT INTO attachments (dataset_id, filename, original_filename, mime_type, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (dataset_id, filename, original_name, uploaded_file.mimetype, timestamp),
            )
    return skipped


def form_payload() -> dict:
    form = request.form
    return {
        "name": form.get("name", "").strip(),
        "description": form.get("description", "").strip(),
        "domain": canonical_value("domain", form.get("domain", "")),
        "languages": join_csv([canonical_value("language", v) for v in form.getlist("languages")]),
        "source": form.get("source", "").strip(),
        "dataset_type": join_csv([canonical_value("dataset_type", v) for v in form.getlist("dataset_type")]),
        "size": form.get("size", "").strip(),
        "format": canonical_value("format", form.get("format", "")),
        "license": form.get("license", "").strip(),
        "notes": form.get("notes", "").strip(),
        "tags": join_csv(split_csv(form.get("tags", ""))),
        "local_path": form.get("local_path", "").strip().strip('"'),
        "favorite": 1 if form.get("favorite") == "on" else 0,
    }


def persist_dataset(existing_id: int | None = None) -> tuple[int, int]:
    payload = form_payload()
    if not payload["name"]:
        raise ValueError("Add a name for the dataset.")

    columns = list(payload.keys())
    timestamp = now_iso()
    with get_db() as connection:
        if existing_id is None:
            cursor = connection.execute(
                f"INSERT INTO datasets ({', '.join(columns)}, created_at, updated_at) "
                f"VALUES ({', '.join('?' * (len(columns) + 2))})",
                [*payload.values(), timestamp, timestamp],
            )
            dataset_id = int(cursor.lastrowid)
        else:
            connection.execute(
                f"UPDATE datasets SET {', '.join(f'{c} = ?' for c in columns)}, updated_at = ? WHERE id = ?",
                [*payload.values(), timestamp, existing_id],
            )
            dataset_id = existing_id

        # Keep option lists in step with any new values.
        for category, column in SINGLE_COLUMNS.items():
            insert_taxonomy(connection, category, payload[column], timestamp)
        for category, column in LIST_COLUMNS.items():
            for value in split_csv(payload[column]):
                insert_taxonomy(connection, category, value, timestamp)

    if existing_id is not None:
        ids = [int(value) for value in request.form.getlist("remove_attachments") if value.isdigit()]
        delete_attachments(dataset_id, ids)

    skipped = save_attachments(dataset_id)
    return dataset_id, skipped


def delete_dataset(dataset_id: int) -> bool:
    dataset = fetch_dataset(dataset_id)
    if dataset is None:
        return False
    for attachment in dataset["attachments"]:
        (UPLOAD_DIR / attachment["filename"]).unlink(missing_ok=True)
    with get_db() as connection:
        connection.execute("DELETE FROM attachments WHERE dataset_id = ?", (dataset_id,))
        connection.execute("DELETE FROM datasets WHERE id = ?", (dataset_id,))
    return True


def form_options(selected: dict) -> dict[str, list[str]]:
    """Taxonomy options plus any values the dataset uses that were removed from the lists."""
    options = get_taxonomy_options()
    extras = {
        "domains": [selected.get("domain", "")],
        "formats": [selected.get("format", "")],
        "languages": selected.get("languages_list", []),
        "dataset_types": selected.get("types_list", []),
    }
    for key, values in extras.items():
        known = {option.lower() for option in options[key]}
        options[key] += [value for value in values if value and value.lower() not in known]
    return options


def form_state_from_request() -> dict:
    payload = form_payload()
    payload["languages_list"] = split_csv(payload["languages"])
    payload["types_list"] = split_csv(payload["dataset_type"])
    return payload


# ---------------------------------------------------------------------------
# Security + template helpers
# ---------------------------------------------------------------------------

def csrf_token() -> str:
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


@app.before_request
def check_csrf() -> None:
    if request.method != "POST":
        return
    token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    if not token or token != session.get("csrf_token"):
        if request.path.startswith("/api/"):
            abort(400, description="Session expired. Reload the page and try again.")
        flash("Your session expired. Reload the page and try again.", "error")
        return redirect(request.referrer or url_for("datasets_index"))


@app.context_processor
def inject_globals() -> dict:
    return {
        "app_name": "Dataset Catalogue",
        "csrf_token": csrf_token,
        "on_vercel": ON_VERCEL,
        "can_open_folders": CAN_OPEN_FOLDERS,
        "language_slots": get_language_slots(),
    }


@app.template_filter("nice_date")
def nice_date(value: str | None) -> str:
    if not value:
        return "Unknown"
    try:
        return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S").strftime("%d %b %Y")
    except ValueError:
        return value


@app.template_filter("compact")
def compact_number(value: int | None) -> str:
    if value is None:
        return "—"
    for limit, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "k")):
        if value >= limit:
            text = f"{value / limit:.1f}".rstrip("0").rstrip(".")
            return f"{text}{suffix}"
    return str(value)


@app.template_filter("thousands")
def thousands(value: int | None) -> str:
    return "—" if value is None else f"{value:,}"


@app.template_filter("host")
def host(value: str | None) -> str:
    return urlparse(value).netloc.removeprefix("www.") if value else ""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def home():
    return redirect(url_for("datasets_index"))


@app.route("/datasets")
def datasets_index() -> str:
    return render_template(
        "index.html",
        overview=get_collection_overview(),
        filter_options=get_taxonomy_options(),
    )


@app.route("/api/datasets")
def datasets_api():
    datasets = query_datasets()
    return jsonify({"datasets": datasets, "count": len(datasets)})


@app.route("/api/datasets/<int:dataset_id>")
def dataset_api(dataset_id: int):
    dataset = fetch_dataset(dataset_id)
    if dataset is None:
        abort(404)
    return jsonify(dataset)


@app.route("/api/datasets/<int:dataset_id>/favorite", methods=["POST"])
def toggle_favorite(dataset_id: int):
    with get_db() as connection:
        row = connection.execute("SELECT favorite FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
        if row is None:
            abort(404)
        favorite = 0 if row["favorite"] else 1
        connection.execute("UPDATE datasets SET favorite = ? WHERE id = ?", (favorite, dataset_id))
    return jsonify({"favorite": bool(favorite)})


@app.route("/api/datasets/<int:dataset_id>/open-folder", methods=["POST"])
def open_dataset_folder(dataset_id: int):
    dataset = fetch_dataset(dataset_id)
    if dataset is None:
        abort(404)
    if not CAN_OPEN_FOLDERS:
        return jsonify({"ok": False, "message": "Folders can only be opened when the app runs on your own computer."}), 400
    folder = resolve_local_path(dataset.get("local_path"))
    if folder is None:
        return jsonify({"ok": False, "message": "No folder saved for this dataset. Add one from Edit."}), 400
    if not folder.exists():
        return jsonify({"ok": False, "message": f"Folder not found: {folder}"}), 404
    if folder.is_file():
        folder = folder.parent
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(folder))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])
    except OSError as error:
        return jsonify({"ok": False, "message": f"Couldn't open the folder: {error}"}), 500
    return jsonify({"ok": True, "message": "Folder opened in your file explorer."})


@app.route("/datasets/new", methods=["GET", "POST"])
def new_dataset() -> str:
    if request.method == "POST":
        try:
            dataset_id, skipped = persist_dataset()
        except ValueError as error:
            flash(str(error), "error")
            state = form_state_from_request()
            return render_template("dataset_form.html", dataset=state, mode="new", options=form_options(state)), 400
        flash("Dataset added.", "success")
        if skipped:
            flash(f"Skipped {skipped} file(s) that weren't images (PNG, JPG, GIF or WebP).", "error")
        return redirect(url_for("dataset_detail", dataset_id=dataset_id))

    return render_template("dataset_form.html", dataset={}, mode="new", options=form_options({}))


@app.route("/datasets/<int:dataset_id>")
def dataset_detail(dataset_id: int) -> str:
    dataset = fetch_dataset(dataset_id)
    if dataset is None:
        abort(404)
    return render_template("dataset_detail.html", dataset=dataset)


@app.route("/datasets/<int:dataset_id>/edit", methods=["GET", "POST"])
def edit_dataset(dataset_id: int) -> str:
    dataset = fetch_dataset(dataset_id)
    if dataset is None:
        abort(404)

    if request.method == "POST":
        try:
            _, skipped = persist_dataset(existing_id=dataset_id)
        except ValueError as error:
            flash(str(error), "error")
            state = form_state_from_request()
            state["id"] = dataset_id
            state["attachments"] = dataset["attachments"]
            return render_template("dataset_form.html", dataset=state, mode="edit", options=form_options(state)), 400
        flash("Changes saved.", "success")
        if skipped:
            flash(f"Skipped {skipped} file(s) that weren't images (PNG, JPG, GIF or WebP).", "error")
        return redirect(url_for("dataset_detail", dataset_id=dataset_id))

    return render_template("dataset_form.html", dataset=dataset, mode="edit", options=form_options(dataset))


@app.route("/datasets/<int:dataset_id>/delete", methods=["POST"])
def remove_dataset(dataset_id: int):
    if not delete_dataset(dataset_id):
        abort(404)
    flash("Dataset deleted.", "success")
    return redirect(url_for("datasets_index"))


@app.route("/uploads/<path:filename>")
def uploaded_file(filename: str):
    return send_from_directory(UPLOAD_DIR, filename)


@app.route("/taxonomy")
def taxonomy_index() -> str:
    groups = []
    for category, label in TAXONOMY_CATEGORIES.items():
        with get_db() as connection:
            rows = connection.execute(
                "SELECT id, value FROM taxonomy WHERE category = ? ORDER BY value COLLATE NOCASE",
                (category,),
            ).fetchall()
        entries = [{"id": row["id"], "value": row["value"], "usage": taxonomy_usage(category, row["value"])} for row in rows]
        groups.append({"category": category, "label": label, "entries": entries})
    return render_template("taxonomy.html", groups=groups)


@app.route("/taxonomy/add", methods=["POST"])
def taxonomy_add():
    category = request.form.get("category", "").strip()
    value = canonical_value(category, request.form.get("value", ""))
    if category not in TAXONOMY_CATEGORIES:
        abort(400)
    if not value:
        flash("Type a value before adding it.", "error")
        return redirect(url_for("taxonomy_index"))
    with get_db() as connection:
        cursor = connection.execute(
            "INSERT OR IGNORE INTO taxonomy (category, value, created_at) VALUES (?, ?, ?)",
            (category, value, now_iso()),
        )
    if cursor.rowcount:
        flash(f"Added “{value}”.", "success")
    else:
        flash(f"“{value}” is already in the list.", "error")
    return redirect(url_for("taxonomy_index", _anchor=category))


@app.route("/taxonomy/<int:taxonomy_id>/edit", methods=["POST"])
def taxonomy_edit(taxonomy_id: int):
    with get_db() as connection:
        item = connection.execute("SELECT * FROM taxonomy WHERE id = ?", (taxonomy_id,)).fetchone()
        if item is None:
            abort(404)
        category, old = item["category"], item["value"]
        new = canonical_value(category, request.form.get("value", ""))
        if not new:
            flash("An option can't be empty. Use Delete to remove it.", "error")
        elif new == old:
            pass
        else:
            existing = connection.execute(
                "SELECT id FROM taxonomy WHERE category = ? AND value = ? COLLATE NOCASE AND id != ?",
                (category, new, taxonomy_id),
            ).fetchone()
            rename_taxonomy_in_datasets(connection, category, old, new)
            if existing:
                connection.execute("DELETE FROM taxonomy WHERE id = ?", (taxonomy_id,))
                flash(f"Merged “{old}” into “{new}”. Datasets were updated.", "success")
            else:
                connection.execute("UPDATE taxonomy SET value = ? WHERE id = ?", (new, taxonomy_id))
                flash(f"Renamed “{old}” to “{new}”. Datasets were updated.", "success")
    return redirect(url_for("taxonomy_index", _anchor=category))


@app.route("/taxonomy/<int:taxonomy_id>/delete", methods=["POST"])
def taxonomy_delete(taxonomy_id: int):
    with get_db() as connection:
        item = connection.execute("SELECT * FROM taxonomy WHERE id = ?", (taxonomy_id,)).fetchone()
    if item is None:
        abort(404)
    usage = taxonomy_usage(item["category"], item["value"])
    if usage:
        flash(f"“{item['value']}” is used by {usage} dataset(s). Rename it to merge, or change those datasets first.", "error")
    else:
        with get_db() as connection:
            connection.execute("DELETE FROM taxonomy WHERE id = ?", (taxonomy_id,))
        flash(f"Deleted “{item['value']}”.", "success")
    return redirect(url_for("taxonomy_index", _anchor=item["category"]))


def export_rows() -> list[dict]:
    rows = []
    for dataset in query_datasets():
        row = {field: dataset.get(field) for field in EXPORT_FIELDS}
        row["favorite"] = "yes" if dataset["favorite"] else "no"
        rows.append(row)
    return rows


@app.route("/export.csv")
def export_csv():
    buffer = io.StringIO()
    buffer.write("﻿")  # BOM so Excel reads Arabic text correctly
    writer = csv.DictWriter(buffer, fieldnames=EXPORT_FIELDS)
    writer.writeheader()
    writer.writerows(export_rows())
    stamp = datetime.now().strftime("%Y-%m-%d")
    return buffer.getvalue(), 200, {
        "Content-Type": "text/csv; charset=utf-8",
        "Content-Disposition": f'attachment; filename="datasets-{stamp}.csv"',
    }


@app.route("/export.json")
def export_json():
    rows = export_rows()
    stamp = datetime.now().strftime("%Y-%m-%d")
    body = json.dumps({"exported_at": now_iso(), "count": len(rows), "datasets": rows}, ensure_ascii=False, indent=2)
    return body, 200, {
        "Content-Type": "application/json; charset=utf-8",
        "Content-Disposition": f'attachment; filename="datasets-{stamp}.json"',
    }


@app.errorhandler(404)
def not_found(error):
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "message": "Not found."}), 404
    return render_template("404.html"), 404


@app.errorhandler(400)
def bad_request(error):
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "message": getattr(error, "description", "Bad request.")}), 400
    return error


@app.errorhandler(RequestEntityTooLarge)
def too_large(error):
    flash("Upload is larger than 20 MB. Add fewer or smaller images at a time.", "error")
    return redirect(request.referrer or url_for("datasets_index"))


bootstrap()

if __name__ == "__main__":
    app.run(debug=True)
