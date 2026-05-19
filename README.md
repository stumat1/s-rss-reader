# S-RSS Reader

A self-hosted, single-user RSS reader with a river-style feed. No auth, no build step — just add feeds and read.

## Features

- **River view** — articles from all feeds in reverse-chronological order, paginated
- **Filtering** — by feed, unread only, or favourites
- **Article actions** — favourite, mark read/unread, dismiss
- **Mark all read** — for the current view
- **Feed management** — add/delete feeds, set per-feed poll interval and category
- **Manual refresh** — refresh one feed or all at once
- **Themes** — customize with multiple theme options
- **OPML import/export** — migrate from/to any other RSS reader
- **Background polling** — each feed fetched on its own schedule (default 30 min)
- **Conditional GET** — uses `ETag`/`Last-Modified` to skip unchanged feeds
- **HTMX frontend** — all interactivity server-rendered, no hand-written JS

<img width="820" height="586" alt="s-rss-reader-screenshot01" src="https://github.com/user-attachments/assets/5e2f84dc-3cc2-405c-bcf6-57c063e35b18" />

<img width="815" height="584" alt="s-rss-reader-screenshot02" src="https://github.com/user-attachments/assets/79246834-ebef-47fe-9842-9ef800a8496d" />

<img width="809" height="589" alt="s-rss-reader-screenshot03" src="https://github.com/user-attachments/assets/091333ca-92c8-4aed-8b94-02c3358c759f" />


## Stack

| Layer       | Technology                                      |
| ----------- | ----------------------------------------------- |
| Backend     | Python 3.12, FastAPI, SQLAlchemy (sync), SQLite |
| Frontend    | HTMX + Jinja2 templates, plain CSS (light/dark themes) |
| RSS parsing | feedparser                                      |
| Scheduling  | APScheduler BackgroundScheduler                 |
| Container   | Docker + docker-compose                         |

## Quick start

**With Docker (recommended):**

```bash
docker compose up --build
```

Open [http://localhost:8080](http://localhost:8080).

Data is persisted to `./data/rss.db` on the host via a volume mount.

**Without Docker:**

```bash
pip install -r requirements.txt
mkdir -p data
DB_PATH=./data/rss.db uvicorn app.main:app --reload
```

Open [http://localhost:8000](http://localhost:8000).

**Running tests:**

```bash
pip install pytest
pytest tests/
```

## Configuration

Copy `.env.example` to `.env` and edit as needed, or set environment variables directly.

| Variable                | Default        | Description                                                           |
| ----------------------- | -------------- | --------------------------------------------------------------------- |
| `DB_PATH`               | `/data/rss.db` | Path to the SQLite database file                                      |
| `FETCH_INTERVAL_MIN`    | `30`           | Default poll interval for new feeds (minutes)                         |
| `MAX_ARTICLES_PER_FEED` | `200`          | Max articles kept per feed; oldest non-favourited articles are pruned |

## Project layout

```
app/
  main.py          — all FastAPI routes
  database.py      — SQLite engine + get_db dependency
  models.py        — Feed, Article ORM models
  fetcher.py       — fetch_feed(), scheduling, URL validation
  templates/
    base.html
    index.html         — article river + sidebar
    feeds.html         — feed management
    settings.html      — OPML import/export
    partials/          — HTMX partial responses
static/style.css
Dockerfile
docker-compose.yml
requirements.txt
```

## OPML

Export your feeds from **Settings → Export OPML**. Categories are preserved in the exported file.

To import, upload an OPML file from **Settings → Import OPML**. Duplicate feeds are skipped automatically.
