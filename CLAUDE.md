# RSS Reader

A self-hosted RSS reader web app. Single-user, no auth, river-style feed.

## Stack
- **Backend**: Python 3.12, FastAPI, SQLAlchemy (sync), SQLite
- **Frontend**: HTMX + Jinja2 templates, plain CSS (dark theme, no build step)
- **RSS parsing**: feedparser
- **Polling**: APScheduler BackgroundScheduler (per-feed interval, default 30 min)
- **Container**: Docker + docker-compose (port 8080 → 8000)

## Project layout
```
app/
  main.py        — all FastAPI routes
  database.py    — SQLite engine + get_db dependency
  models.py      — Feed, Article ORM models
  fetcher.py     — fetch_feed(), schedule_feed(), start_scheduler()
  templates/
    base.html
    index.html         — article river with sidebar
    feeds.html         — feed management
    partials/
      article_list.html
      article_card.html
      feed_list.html
static/style.css
Dockerfile
docker-compose.yml
requirements.txt
```

## Running locally (without Docker)
```bash
pip install -r requirements.txt
mkdir -p data
DB_PATH=./data/rss.db uvicorn app.main:app --reload
```

## Running with Docker
```bash
docker compose up --build
# → http://localhost:8080
```

## Environment variables
| Variable | Default | Purpose |
|---|---|---|
| `DB_PATH` | `/data/rss.db` | SQLite file path |
| `FETCH_INTERVAL_MIN` | `30` | Poll interval for new feeds (minutes) |
| `MAX_ARTICLES_PER_FEED` | `200` | Articles kept per feed (oldest non-fav pruned) |

## Key design decisions
- Articles are **soft-deleted** (`is_deleted` flag) — scheduler never trips over missing rows
- Dedup key is `(feed_id, guid)` — safe to re-fetch the same feed repeatedly
- Favourited articles are **excluded from pruning**
- First fetch on `POST /feeds` runs in a background thread so the HTTP response is immediate
- HTMX handles all interactivity — no hand-written JS
