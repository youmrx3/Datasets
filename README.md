# Dataset Catalog

A lightweight local web app for collecting and managing research datasets, with a focus on multilingual mental health resources but flexible enough for other domains.

## Features

- Add, edit, delete, and view datasets
- Search by name, description, notes, source, or tags
- Filter by domain, language, dataset type, and favorites
- Card and table views
- Local SQLite storage
- Image / screenshot attachments per dataset
- CSV and JSON export

## Fields

Each dataset stores:

- Name
- Description
- Domain
- Languages
- Source
- Dataset type
- Size
- Format
- License
- Notes
- Tags
- Favorite flag
- Screenshots or images

## Run locally

1. Create and activate a virtual environment if you want one.
2. Install the dependency:

```bash
pip install -r requirements.txt
```

3. Start the app:

```bash
python app.py
```

4. Open the app in your browser at:

```text
http://127.0.0.1:5000
```

## Data storage

- SQLite database: `instance/datasets.db`
- Uploaded images: `uploads/`

Both are created automatically on first run.
