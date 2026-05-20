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

--

<img width="821" height="587" alt="Screenshot 2026-05-19 152500" src="https://github.com/user-attachments/assets/75b2ec12-c0e2-4077-8dfc-00fc4dc4ddad" />

<img width="812" height="584" alt="Screenshot 2026-05-19 152556" src="https://github.com/user-attachments/assets/65f9c088-b99d-4e14-af4f-b2cd12723700" />

<img width="817" height="584" alt="Screenshot 2026-05-19 152527" src="https://github.com/user-attachments/assets/16dd6298-e234-40f7-88c2-4eef914fd727" />

--

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

## Configuration

Copy `.env.example` to `.env` and edit as needed, or set environment variables directly.

| Variable                | Default        | Description                                                           |
| ----------------------- | -------------- | --------------------------------------------------------------------- |
| `DB_PATH`               | `/data/rss.db` | Path to the SQLite database file                                      |
| `FETCH_INTERVAL_MIN`    | `30`           | Default poll interval for new feeds (minutes)                         |
| `MAX_ARTICLES_PER_FEED` | `200`          | Max articles kept per feed; oldest non-favourited articles are pruned |

## OPML

Export your feeds from **Settings → Export OPML**. Categories are preserved in the exported file.

To import, upload an OPML file from **Settings → Import OPML**. Duplicate feeds are skipped automatically.
