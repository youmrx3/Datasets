# Dataset Catalogue

A local web app for cataloguing research datasets, built around multilingual
mental-health corpora (Arabic, Darija, English) but usable for any domain.

## Features

- **Corpus spectrum:** every dataset as a bar on a log-scale sample-count axis,
  coloured by language. The legend doubles as a language filter.
- Search across names, descriptions, notes, sources, tags, languages and formats.
  Press `/` to jump to search.
- Filter by language, modality, format, domain and starred datasets. Filters
  live in the URL, so a filtered view can be bookmarked.
- Sort by date added, date edited, name or sample count.
- Card and table layouts. Star datasets with one click.
- Multiple languages and modalities per dataset.
- Screenshots: drag and drop, pick files, or paste straight from the clipboard.
- Open a dataset's local folder in Explorer from the catalogue.
- Options page: rename a language, format, etc. and every dataset updates.
  Rename to an existing value to merge the two.
- CSV (Excel-friendly, UTF-8 BOM for Arabic) and JSON export of the current view.
- Light and dark themes.

## Run locally

```bash
pip install -r requirements.txt
python app.py
```

Then open <http://127.0.0.1:5000>.

## Where things are stored

| What | Where |
|---|---|
| Database | `instance/datasets.db` |
| Uploaded images | `uploads/` |
| Downloaded dataset files | `downloaded datasets/` (override with the `DATASETS_ROOT` environment variable) |

The **Local folder** field accepts a folder name inside `downloaded datasets`
(recommended: it keeps working if you move the project) or a full path.

## Size field

Write the sample count first, e.g. `35,648 consultations / 206 MB`. The app reads
the first number (with `k`/`M` suffixes and `16.084`-style thousands separators)
to draw the spectrum and sort by size.

## Deploying to Vercel

Vercel has no persistent disk, so the online copy starts from `seed_data.json`
each time and edits there are temporary. To publish your local catalogue:

```bash
python scripts/export_seed.py
```

then commit `seed_data.json` and `seed_uploads/`. Set a `SECRET_KEY` environment
variable in Vercel.

## Maintenance scripts

- `scripts/export_seed.py`: export the local database for Vercel.
- `scripts/normalize_data.py [--dry-run]`: one-off clean-up (casing, combined
  values, portable folder paths). Backs up the database first.
