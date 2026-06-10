from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
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
    url_for,
)
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
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dataset-catalog-secret")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024


def ensure_directories() -> None:
    INSTANCE_DIR.mkdir(exist_ok=True)
    UPLOAD_DIR.mkdir(exist_ok=True)


def get_db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    ensure_directories()
    with get_db() as connection:
        connection.execute(
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
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dataset_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                original_filename TEXT NOT NULL,
                mime_type TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(dataset_id) REFERENCES datasets(id) ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS taxonomy (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                value TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(category, value COLLATE NOCASE)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        columns = {row[1] for row in connection.execute("PRAGMA table_info(datasets)").fetchall()}
        if "local_path" not in columns:
            connection.execute("ALTER TABLE datasets ADD COLUMN local_path TEXT")

        seeded = connection.execute(
            "SELECT value FROM settings WHERE key = ?",
            ("taxonomy_seeded",),
        ).fetchone()
        if not seeded:
            connection.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)",
                ("taxonomy_seeded", "0"),
            )
        format_seeded = connection.execute(
            "SELECT value FROM settings WHERE key = ?",
            ("format_seeded",),
        ).fetchone()
        if not format_seeded:
            connection.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)",
                ("format_seeded", "0"),
            )


@app.before_request
def prepare_database() -> None:
    init_db()
    seed_taxonomy_if_needed()
    sync_taxonomy_from_datasets()
    seed_default_formats()


@app.context_processor
def inject_now() -> dict[str, str]:
    return {"app_name": "Dataset Catalog"}


def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def join_csv(values: list[str]) -> str:
    return ", ".join([value.strip() for value in values if value.strip()])


def path_to_file_url(value: str | None) -> str | None:
    if not value:
        return None
    try:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = (BASE_DIR / path).resolve()
        else:
            path = path.resolve()
        if path.is_dir():
            return path.as_uri()
        if path.exists():
            return path.parent.resolve().as_uri()
        return path.as_uri() if path.exists() else None
    except (OSError, ValueError):
        return None


def allowed_image(filename: str) -> bool:
    if "." not in filename:
        return False
    return filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def dataset_from_row(row: sqlite3.Row, include_attachments: bool = False) -> dict:
    dataset = dict(row)
    dataset["favorite"] = bool(dataset["favorite"])
    dataset["languages_list"] = split_csv(dataset.get("languages"))
    dataset["tags_list"] = split_csv(dataset.get("tags"))
    dataset["folder_url"] = path_to_file_url(dataset.get("local_path"))
    dataset["folder_open_url"] = url_for("open_dataset_folder", dataset_id=dataset["id"])
    if include_attachments:
        dataset["attachments"] = get_attachments(dataset["id"])
    return dataset


def seed_taxonomy_if_needed() -> None:
    with get_db() as connection:
        row = connection.execute(
            "SELECT value FROM settings WHERE key = ?",
            ("taxonomy_seeded",),
        ).fetchone()
        if not row or row["value"] == "1":
            return

        datasets = connection.execute(
            "SELECT domain, dataset_type, languages, format FROM datasets"
        ).fetchall()

        timestamp = now_iso()
        for dataset in datasets:
            if dataset["domain"]:
                insert_taxonomy(connection, "domain", dataset["domain"], timestamp)
            if dataset["dataset_type"]:
                insert_taxonomy(connection, "dataset_type", dataset["dataset_type"], timestamp)
            if dataset["format"]:
                insert_taxonomy(connection, "format", dataset["format"], timestamp)
            for language in split_csv(dataset["languages"]):
                insert_taxonomy(connection, "language", language, timestamp)

        connection.execute(
            "UPDATE settings SET value = ? WHERE key = ?",
            ("1", "taxonomy_seeded"),
        )


def sync_taxonomy_from_datasets() -> None:
    with get_db() as connection:
        datasets = connection.execute(
            "SELECT domain, dataset_type, languages, format FROM datasets"
        ).fetchall()

        timestamp = now_iso()
        for dataset in datasets:
            if dataset["domain"]:
                insert_taxonomy(connection, "domain", dataset["domain"], timestamp)
            if dataset["dataset_type"]:
                insert_taxonomy(connection, "dataset_type", dataset["dataset_type"], timestamp)
            if dataset["format"]:
                insert_taxonomy(connection, "format", dataset["format"], timestamp)
            for language in split_csv(dataset["languages"]):
                insert_taxonomy(connection, "language", language, timestamp)


def seed_default_formats() -> None:
    default_formats = [
        "CSV",
        "JSON",
        "TSV",
        "Parquet",
        "XLSX",
        "TXT",
        "XML",
    ]
    with get_db() as connection:
        row = connection.execute(
            "SELECT value FROM settings WHERE key = ?",
            ("format_seeded",),
        ).fetchone()
        if row and row["value"] == "1":
            return

        timestamp = now_iso()
        for fmt in default_formats:
            insert_taxonomy(connection, "format", fmt, timestamp)

        connection.execute(
            "UPDATE settings SET value = ? WHERE key = ?",
            ("1", "format_seeded"),
        )


def insert_taxonomy(connection: sqlite3.Connection, category: str, value: str, timestamp: str) -> None:
    cleaned = value.strip()
    if not cleaned:
        return
    connection.execute(
        """
        INSERT OR IGNORE INTO taxonomy (category, value, created_at)
        VALUES (?, ?, ?)
        """,
        (category, cleaned, timestamp),
    )


def get_taxonomy_values(category: str) -> list[str]:
    with get_db() as connection:
        rows = connection.execute(
            """
            SELECT value
            FROM taxonomy
            WHERE category = ?
            ORDER BY value COLLATE NOCASE ASC
            """,
            (category,),
        ).fetchall()
    return [row["value"] for row in rows]


def get_taxonomy_entries(category: str) -> list[dict]:
    with get_db() as connection:
        rows = connection.execute(
            """
            SELECT id, value
            FROM taxonomy
            WHERE category = ?
            ORDER BY value COLLATE NOCASE ASC
            """,
            (category,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_taxonomy_options() -> dict[str, list[str]]:
    return {
        "domains": get_taxonomy_values("domain"),
        "dataset_types": get_taxonomy_values("dataset_type"),
        "languages": get_taxonomy_values("language"),
        "formats": get_taxonomy_values("format"),
    }


def merge_options(options: list[str], selected: list[str]) -> list[str]:
    merged = list(options)
    for value in selected:
        if value and value not in merged:
            merged.append(value)
    return merged


def get_attachments(dataset_id: int) -> list[dict]:
    with get_db() as connection:
        rows = connection.execute(
            """
            SELECT id, dataset_id, filename, original_filename, mime_type, created_at
            FROM attachments
            WHERE dataset_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            (dataset_id,),
        ).fetchall()
    attachments = []
    for row in rows:
        attachment = dict(row)
        attachment["url"] = url_for("uploaded_file", filename=attachment["filename"])
        attachments.append(attachment)
    return attachments


def get_attachment_by_ids(dataset_id: int, attachment_ids: list[int]) -> list[dict]:
    if not attachment_ids:
        return []

    placeholders = ",".join(["?"] * len(attachment_ids))
    query = f"""
        SELECT id, dataset_id, filename, original_filename, mime_type, created_at
        FROM attachments
        WHERE dataset_id = ? AND id IN ({placeholders})
    """
    with get_db() as connection:
        rows = connection.execute(query, [dataset_id, *attachment_ids]).fetchall()
    return [dict(row) for row in rows]


def delete_attachments(dataset_id: int, attachment_ids: list[int]) -> None:
    attachments = get_attachment_by_ids(dataset_id, attachment_ids)
    if not attachments:
        return

    with get_db() as connection:
        for attachment in attachments:
            file_path = UPLOAD_DIR / attachment["filename"]
            if file_path.exists():
                file_path.unlink()
        placeholders = ",".join(["?"] * len(attachments))
        connection.execute(
            f"DELETE FROM attachments WHERE dataset_id = ? AND id IN ({placeholders})",
            [dataset_id, *[attachment["id"] for attachment in attachments]],
        )


def get_filter_options() -> dict[str, list[str]]:
    return get_taxonomy_options()


def get_dashboard_counts() -> dict[str, int]:
    with get_db() as connection:
        row = connection.execute(
            """
            SELECT
                COUNT(*) AS total,
                COALESCE(SUM(CASE WHEN favorite = 1 THEN 1 ELSE 0 END), 0) AS favorites,
                COALESCE(COUNT(DISTINCT domain), 0) AS domains
            FROM datasets
            """
        ).fetchone()
    return {"total": row["total"], "favorites": row["favorites"], "domains": row["domains"]}


def fetch_dataset(dataset_id: int) -> dict | None:
    with get_db() as connection:
        row = connection.execute(
            "SELECT * FROM datasets WHERE id = ?",
            (dataset_id,),
        ).fetchone()
    if row is None:
        return None
    return dataset_from_row(row, include_attachments=True)


def query_datasets() -> list[dict]:
    search = request.args.get("q", "").strip()
    domain = request.args.get("domain", "").strip()
    language = request.args.get("language", "").strip()
    dataset_type = request.args.get("dataset_type", "").strip()
    dataset_format = request.args.get("format", "").strip()
    sort = request.args.get("sort", "created_desc").strip()
    favorite_only = request.args.get("favorite", "").strip() == "1"

    clauses = []
    params: list[str | int] = []

    if search:
        search_value = f"%{search.lower()}%"
        clauses.append(
            "(" 
            "LOWER(name) LIKE ? OR LOWER(COALESCE(description, '')) LIKE ? OR "
            "LOWER(COALESCE(notes, '')) LIKE ? OR LOWER(COALESCE(tags, '')) LIKE ? OR "
            "LOWER(COALESCE(source, '')) LIKE ?"
            ")"
        )
        params.extend([search_value] * 5)
    if domain:
        clauses.append("LOWER(COALESCE(domain, '')) = LOWER(?)")
        params.append(domain)
    if language:
        clauses.append("LOWER(COALESCE(languages, '')) LIKE ?")
        params.append(f"%{language.lower()}%")
    if dataset_type:
        clauses.append("LOWER(COALESCE(dataset_type, '')) = LOWER(?)")
        params.append(dataset_type)
    if dataset_format:
        clauses.append("LOWER(COALESCE(format, '')) = LOWER(?)")
        params.append(dataset_format)
    if favorite_only:
        clauses.append("favorite = 1")

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

    return [dataset_from_row(row) for row in rows]


def save_attachments(dataset_id: int) -> None:
    uploaded_files = request.files.getlist("screenshots")
    if not uploaded_files:
        return

    timestamp = now_iso()
    with get_db() as connection:
        for uploaded_file in uploaded_files:
            if not uploaded_file or not uploaded_file.filename:
                continue
            if not allowed_image(uploaded_file.filename):
                continue
            original_name = uploaded_file.filename
            safe_name = secure_filename(original_name)
            filename = f"{dataset_id}_{uuid4().hex}_{safe_name}"
            file_path = UPLOAD_DIR / filename
            uploaded_file.save(file_path)
            connection.execute(
                """
                INSERT INTO attachments (dataset_id, filename, original_filename, mime_type, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    dataset_id,
                    filename,
                    original_name,
                    uploaded_file.mimetype,
                    timestamp,
                ),
            )


def persist_dataset(existing_id: int | None = None) -> int:
    language_values = request.form.getlist("languages")
    if not language_values:
        language_values = split_csv(request.form.get("languages", ""))

    payload = {
        "name": request.form.get("name", "").strip(),
        "description": request.form.get("description", "").strip(),
        "domain": request.form.get("domain", "").strip(),
        "languages": join_csv(language_values),
        "source": request.form.get("source", "").strip(),
        "dataset_type": request.form.get("dataset_type", "").strip(),
        "size": request.form.get("size", "").strip(),
        "format": request.form.get("format", "").strip(),
        "license": request.form.get("license", "").strip(),
        "notes": request.form.get("notes", "").strip(),
        "tags": join_csv(split_csv(request.form.get("tags", ""))),
        "local_path": request.form.get("local_path", "").strip(),
        "favorite": 1 if request.form.get("favorite") == "on" else 0,
    }

    if not payload["name"]:
        raise ValueError("Name is required.")

    timestamp = now_iso()
    with get_db() as connection:
        if existing_id is None:
            cursor = connection.execute(
                """
                INSERT INTO datasets (
                    name, description, domain, languages, source, dataset_type,
                    size, format, license, notes, tags, local_path, favorite, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["name"],
                    payload["description"],
                    payload["domain"],
                    payload["languages"],
                    payload["source"],
                    payload["dataset_type"],
                    payload["size"],
                    payload["format"],
                    payload["license"],
                    payload["notes"],
                    payload["tags"],
                    payload["local_path"],
                    payload["favorite"],
                    timestamp,
                    timestamp,
                ),
            )
            dataset_id = int(cursor.lastrowid)
        else:
            connection.execute(
                """
                UPDATE datasets
                SET name = ?, description = ?, domain = ?, languages = ?, source = ?,
                    dataset_type = ?, size = ?, format = ?, license = ?, notes = ?,
                    tags = ?, local_path = ?, favorite = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    payload["name"],
                    payload["description"],
                    payload["domain"],
                    payload["languages"],
                    payload["source"],
                    payload["dataset_type"],
                    payload["size"],
                    payload["format"],
                    payload["license"],
                    payload["notes"],
                    payload["tags"],
                    payload["local_path"],
                    payload["favorite"],
                    timestamp,
                    existing_id,
                ),
            )
            dataset_id = existing_id

    if existing_id is not None:
        attachment_ids = [int(value) for value in request.form.getlist("remove_attachments") if value.isdigit()]
        delete_attachments(dataset_id, attachment_ids)

    save_attachments(dataset_id)
    return dataset_id


def delete_dataset(dataset_id: int) -> bool:
    dataset = fetch_dataset(dataset_id)
    if dataset is None:
        return False

    for attachment in dataset["attachments"]:
        file_path = UPLOAD_DIR / attachment["filename"]
        if file_path.exists():
            file_path.unlink()

    with get_db() as connection:
        connection.execute("DELETE FROM attachments WHERE dataset_id = ?", (dataset_id,))
        connection.execute("DELETE FROM datasets WHERE id = ?", (dataset_id,))

    return True


@app.route("/")
def home() -> str:
    return redirect(url_for("datasets_index"))


@app.route("/datasets")
def datasets_index() -> str:
    return render_template(
        "index.html",
        stats=get_dashboard_counts(),
        filter_options=get_filter_options(),
    )


@app.route("/api/datasets")
def datasets_api() -> tuple[str, int] | tuple[dict, int]:
    datasets = query_datasets()
    return jsonify({"datasets": datasets, "count": len(datasets)})


@app.route("/api/datasets/<int:dataset_id>")
def dataset_api(dataset_id: int):
    dataset = fetch_dataset(dataset_id)
    if dataset is None:
        abort(404)
    return jsonify(dataset)


@app.route("/datasets/new", methods=["GET", "POST"])
def new_dataset() -> str:
    if request.method == "POST":
        try:
            dataset_id = persist_dataset()
        except ValueError as error:
            flash(str(error), "error")
            selected_languages = request.form.getlist("languages") or split_csv(request.form.get("languages", ""))
            taxonomy_options = get_taxonomy_options()
            return render_template(
                "dataset_form.html",
                dataset=request.form,
                mode="new",
                taxonomy_options=taxonomy_options,
                selected_languages=selected_languages,
            )

        flash("Dataset created.", "success")
        return redirect(url_for("dataset_detail", dataset_id=dataset_id))

    taxonomy_options = get_taxonomy_options()
    return render_template(
        "dataset_form.html",
        dataset={},
        mode="new",
        taxonomy_options=taxonomy_options,
        selected_languages=[],
    )


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
            persist_dataset(existing_id=dataset_id)
        except ValueError as error:
            flash(str(error), "error")
            selected_languages = request.form.getlist("languages") or split_csv(request.form.get("languages", ""))
            taxonomy_options = get_taxonomy_options()
            taxonomy_options["languages"] = merge_options(taxonomy_options["languages"], selected_languages)
            taxonomy_options["domains"] = merge_options(taxonomy_options["domains"], [request.form.get("domain", "").strip()])
            taxonomy_options["dataset_types"] = merge_options(
                taxonomy_options["dataset_types"],
                [request.form.get("dataset_type", "").strip()],
            )
            taxonomy_options["formats"] = merge_options(
                taxonomy_options["formats"],
                [request.form.get("format", "").strip()],
            )
            return render_template(
                "dataset_form.html",
                dataset=request.form,
                mode="edit",
                current_dataset=dataset,
                taxonomy_options=taxonomy_options,
                selected_languages=selected_languages,
            )

        flash("Dataset updated.", "success")
        return redirect(url_for("dataset_detail", dataset_id=dataset_id))

    selected_languages = split_csv(dataset.get("languages", ""))
    taxonomy_options = get_taxonomy_options()
    taxonomy_options["languages"] = merge_options(taxonomy_options["languages"], selected_languages)
    taxonomy_options["domains"] = merge_options(taxonomy_options["domains"], [dataset.get("domain", "")])
    taxonomy_options["dataset_types"] = merge_options(
        taxonomy_options["dataset_types"],
        [dataset.get("dataset_type", "")],
    )
    taxonomy_options["formats"] = merge_options(
        taxonomy_options["formats"],
        [dataset.get("format", "")],
    )
    return render_template(
        "dataset_form.html",
        dataset=dataset,
        mode="edit",
        current_dataset=dataset,
        taxonomy_options=taxonomy_options,
        selected_languages=selected_languages,
    )


@app.route("/datasets/<int:dataset_id>/delete", methods=["POST"])
def remove_dataset(dataset_id: int):
    if not delete_dataset(dataset_id):
        abort(404)
    flash("Dataset deleted.", "success")
    return redirect(url_for("datasets_index"))


@app.route("/datasets/<int:dataset_id>/open-folder", methods=["GET"])
def open_dataset_folder(dataset_id: int):
    dataset = fetch_dataset(dataset_id)
    if dataset is None:
        abort(404)

    local_path = dataset.get("local_path")
    if not local_path:
        flash("No local folder path saved for this dataset.", "error")
        return redirect(url_for("dataset_detail", dataset_id=dataset_id))

    folder_path = Path(local_path).expanduser()
    if not folder_path.is_absolute():
        folder_path = (BASE_DIR / folder_path).resolve()
    else:
        folder_path = folder_path.resolve()

    if not folder_path.exists():
        flash("The saved folder path does not exist on this PC.", "error")
        return redirect(url_for("dataset_detail", dataset_id=dataset_id))

    if folder_path.is_file():
        folder_path = folder_path.parent

    try:
        os.startfile(str(folder_path))
    except OSError:
        try:
            subprocess.Popen(["explorer", str(folder_path)])
        except OSError:
            flash("Could not open the folder on this PC.", "error")
            return redirect(url_for("dataset_detail", dataset_id=dataset_id))

    flash("Opened the saved folder.", "success")
    return redirect(url_for("dataset_detail", dataset_id=dataset_id))


@app.route("/uploads/<path:filename>")
def uploaded_file(filename: str):
    return send_from_directory(UPLOAD_DIR, filename)


@app.route("/taxonomy", methods=["GET"])
def taxonomy_index() -> str:
    taxonomy = {
        "domains": get_taxonomy_entries("domain"),
        "languages": get_taxonomy_entries("language"),
        "dataset_types": get_taxonomy_entries("dataset_type"),
        "formats": get_taxonomy_entries("format"),
    }
    return render_template("taxonomy.html", taxonomy=taxonomy)


@app.route("/taxonomy/add", methods=["POST"])
def taxonomy_add():
    category = request.form.get("category", "").strip()
    value = request.form.get("value", "").strip()
    if not category or not value:
        flash("Please provide a value.", "error")
        return redirect(url_for("taxonomy_index"))

    with get_db() as connection:
        insert_taxonomy(connection, category, value, now_iso())

    flash("Added taxonomy option.", "success")
    return redirect(url_for("taxonomy_index"))


@app.route("/taxonomy/<int:taxonomy_id>/edit", methods=["POST"])
def taxonomy_edit(taxonomy_id: int):
    value = request.form.get("value", "").strip()
    if not value:
        flash("Value cannot be empty.", "error")
        return redirect(url_for("taxonomy_index"))

    try:
        with get_db() as connection:
            connection.execute(
                "UPDATE taxonomy SET value = ? WHERE id = ?",
                (value, taxonomy_id),
            )
    except sqlite3.IntegrityError:
        flash("That option already exists.", "error")
        return redirect(url_for("taxonomy_index"))

    flash("Updated taxonomy option.", "success")
    return redirect(url_for("taxonomy_index"))


@app.route("/taxonomy/<int:taxonomy_id>/delete", methods=["POST"])
def taxonomy_delete(taxonomy_id: int):
    with get_db() as connection:
        connection.execute("DELETE FROM taxonomy WHERE id = ?", (taxonomy_id,))
    flash("Deleted taxonomy option.", "success")
    return redirect(url_for("taxonomy_index"))


@app.route("/export.csv")
def export_csv():
    datasets = query_datasets()
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=[
            "name",
            "description",
            "domain",
            "languages",
            "source",
            "dataset_type",
            "size",
            "format",
            "license",
            "notes",
            "tags",
                "local_path",
            "favorite",
            "created_at",
            "updated_at",
        ],
    )
    writer.writeheader()
    for dataset in datasets:
        writer.writerow(
            {
                "name": dataset["name"],
                "description": dataset["description"],
                "domain": dataset["domain"],
                "languages": join_csv(dataset["languages_list"]),
                "source": dataset["source"],
                "dataset_type": dataset["dataset_type"],
                "size": dataset["size"],
                "format": dataset["format"],
                "license": dataset["license"],
                "notes": dataset["notes"],
                "tags": join_csv(dataset["tags_list"]),
                "local_path": dataset["local_path"],
                "favorite": "yes" if dataset["favorite"] else "no",
                "created_at": dataset["created_at"],
                "updated_at": dataset["updated_at"],
            }
        )

    return (
        buffer.getvalue(),
        200,
        {
            "Content-Type": "text/csv; charset=utf-8",
            "Content-Disposition": 'attachment; filename="datasets.csv"',
        },
    )


@app.route("/export.json")
def export_json():
    datasets = query_datasets()
    return jsonify({"datasets": datasets, "count": len(datasets)})


@app.errorhandler(404)
def not_found(error):
    return render_template("404.html"), 404


if __name__ == "__main__":
    app.run(debug=True)
