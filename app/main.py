import logging
import os
import threading
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import func, text
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import Base, engine, get_db
from app.fetcher import fetch_feed, schedule_feed, start_scheduler, unschedule_feed, _assert_public_url
from app.models import Article, Feed

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

PAGE_SIZE = 50
_import_executor = ThreadPoolExecutor(max_workers=5)

BASE_DIR = Path(__file__).parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    with engine.connect() as conn:
        for stmt in [
            "ALTER TABLE articles ADD COLUMN is_read BOOLEAN NOT NULL DEFAULT 0",
            "ALTER TABLE feeds ADD COLUMN category TEXT",
            "ALTER TABLE feeds ADD COLUMN etag TEXT",
            "ALTER TABLE feeds ADD COLUMN last_modified TEXT",
        ]:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                pass  # column already exists
    start_scheduler()
    yield
    _import_executor.shutdown(wait=False)


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR.parent / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def require_htmx(request: Request) -> None:
    """Dependency: reject mutation requests that didn't come from HTMX (CSRF guard)."""
    if request.headers.get("HX-Request") != "true":
        raise HTTPException(status_code=403, detail="Direct form submission not allowed")


# ---------------------------------------------------------------------------
# Article river
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    feed_id: int | None = None,
    favourites: int = 0,
    unread: int = 0,
    db: Session = Depends(get_db),
):
    feeds = db.query(Feed).order_by(Feed.title).all()
    articles = _query_articles(db, feed_id=feed_id, favourites=bool(favourites), unread=bool(unread))
    unread_counts = dict(
        db.query(Article.feed_id, func.count(Article.id))
        .filter(Article.is_deleted == False, Article.is_read == False)  # noqa: E712
        .group_by(Article.feed_id)
        .all()
    )
    total_unread = sum(unread_counts.values())
    total_favourites = (
        db.query(func.count(Article.id))
        .filter(Article.is_deleted == False, Article.is_favourite == True)  # noqa: E712
        .scalar()
    )
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "feeds": feeds,
            "articles": articles,
            "active_feed_id": feed_id,
            "show_favourites": bool(favourites),
            "show_unread": bool(unread),
            "unread_counts": unread_counts,
            "total_unread": total_unread,
            "total_favourites": total_favourites,
            "page_size": PAGE_SIZE,
        },
    )


@app.get("/articles", response_class=HTMLResponse)
def articles_partial(
    request: Request,
    feed_id: int | None = None,
    favourites: int = 0,
    unread: int = 0,
    db: Session = Depends(get_db),
):
    articles = _query_articles(db, feed_id=feed_id, favourites=bool(favourites), unread=bool(unread))
    return templates.TemplateResponse(
        "partials/article_list.html",
        {
            "request": request,
            "articles": articles,
            "active_feed_id": feed_id,
            "show_favourites": bool(favourites),
            "show_unread": bool(unread),
            "page_size": PAGE_SIZE,
        },
    )


@app.get("/articles/more", response_class=HTMLResponse)
def articles_more(
    request: Request,
    feed_id: int | None = None,
    favourites: int = 0,
    unread: int = 0,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    articles = _query_articles(
        db,
        feed_id=feed_id,
        favourites=bool(favourites),
        unread=bool(unread),
        offset=offset,
    )
    return templates.TemplateResponse(
        "partials/article_more.html",
        {
            "request": request,
            "articles": articles,
            "active_feed_id": feed_id,
            "show_favourites": bool(favourites),
            "show_unread": bool(unread),
            "offset": offset,
            "page_size": PAGE_SIZE,
        },
    )


def _query_articles(
    db: Session,
    feed_id: int | None = None,
    favourites: bool = False,
    unread: bool = False,
    offset: int = 0,
):
    q = (
        db.query(Article)
        .join(Feed)
        .filter(Article.is_deleted == False)  # noqa: E712
    )
    if feed_id:
        q = q.filter(Article.feed_id == feed_id)
    if favourites:
        q = q.filter(Article.is_favourite == True)  # noqa: E712
    if unread:
        q = q.filter(Article.is_read == False)  # noqa: E712
    return (
        q.order_by(
            Article.published_at.desc().nullslast(),
            Article.fetched_at.desc(),
        )
        .limit(PAGE_SIZE + 1)
        .offset(offset)
        .all()
    )


# ---------------------------------------------------------------------------
# Article actions
# ---------------------------------------------------------------------------

@app.patch("/articles/{article_id}/favourite", response_class=HTMLResponse)
def toggle_favourite(
    article_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_htmx),
):
    article = db.get(Article, article_id)
    if article is None:
        return Response(status_code=404)
    article.is_favourite = not article.is_favourite
    db.commit()
    total_favourites = (
        db.query(func.count(Article.id))
        .filter(Article.is_deleted == False, Article.is_favourite == True)  # noqa: E712
        .scalar()
    )
    return templates.TemplateResponse(
        "partials/favourite_toggle.html",
        {"request": request, "article": article, "total_favourites": total_favourites},
    )


@app.patch("/articles/read-all")
def mark_all_read(
    request: Request,
    feed_id: int | None = None,
    favourites: int = 0,
    db: Session = Depends(get_db),
    _: None = Depends(require_htmx),
):
    q = db.query(Article).filter(
        Article.is_deleted == False,  # noqa: E712
        Article.is_read == False,  # noqa: E712
    )
    if feed_id:
        q = q.filter(Article.feed_id == feed_id)
    if favourites:
        q = q.filter(Article.is_favourite == True)  # noqa: E712
    q.update({"is_read": True}, synchronize_session=False)
    db.commit()
    return Response(status_code=200, headers={"HX-Refresh": "true"})


@app.patch("/articles/{article_id}/read", response_class=HTMLResponse)
def toggle_read(
    article_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_htmx),
):
    article = db.get(Article, article_id)
    if article is None:
        return Response(status_code=404)
    article.is_read = not article.is_read
    db.commit()
    total_unread = (
        db.query(func.count(Article.id))
        .filter(Article.is_deleted == False, Article.is_read == False)  # noqa: E712
        .scalar()
    )
    feed_unread = (
        db.query(func.count(Article.id))
        .filter(
            Article.is_deleted == False,  # noqa: E712
            Article.is_read == False,  # noqa: E712
            Article.feed_id == article.feed_id,
        )
        .scalar()
    )
    return templates.TemplateResponse(
        "partials/read_toggle.html",
        {
            "request": request,
            "article": article,
            "total_unread": total_unread,
            "feed_unread": feed_unread,
        },
    )


@app.delete("/articles/{article_id}")
def delete_article(
    article_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_htmx),
):
    article = db.get(Article, article_id)
    if article is None:
        return Response(status_code=404)
    article.is_deleted = True
    db.commit()
    return Response(status_code=200)


# ---------------------------------------------------------------------------
# Settings page
# ---------------------------------------------------------------------------

@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    return templates.TemplateResponse("settings.html", {"request": request})


@app.get("/settings/export")
def export_opml(db: Session = Depends(get_db)):
    feeds = db.query(Feed).order_by(Feed.title).all()

    root = ET.Element("opml", version="2.0")
    head = ET.SubElement(root, "head")
    ET.SubElement(head, "title").text = "S-RSS Reader Feeds"
    body = ET.SubElement(root, "body")

    def _feed_attrs(feed: Feed) -> dict:
        attrs = {
            "type": "rss",
            "text": feed.title or feed.url,
            "title": feed.title or feed.url,
            "xmlUrl": feed.url,
        }
        if feed.site_url:
            attrs["htmlUrl"] = feed.site_url
        return attrs

    by_cat: dict[str, list] = defaultdict(list)
    for feed in feeds:
        by_cat[feed.category or ""].append(feed)

    for feed in by_cat.get("", []):
        ET.SubElement(body, "outline", **_feed_attrs(feed))

    for cat_name in sorted(k for k in by_cat if k):
        cat_el = ET.SubElement(body, "outline", text=cat_name, title=cat_name)
        for feed in by_cat[cat_name]:
            ET.SubElement(cat_el, "outline", **_feed_attrs(feed))

    xml_bytes = (
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        + ET.tostring(root, encoding="unicode").encode("utf-8")
    )
    return Response(
        content=xml_bytes,
        media_type="application/xml",
        headers={"Content-Disposition": 'attachment; filename="feeds.opml"'},
    )


@app.post("/settings/import", response_class=HTMLResponse)
def import_opml(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: None = Depends(require_htmx),
):
    MAX_UPLOAD_BYTES = 2 * 1024 * 1024  # 2 MB
    content = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        return templates.TemplateResponse(
            "partials/import_result.html",
            {"request": request, "error": "File too large (max 2 MB).", "imported": 0, "skipped": 0},
        )
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return templates.TemplateResponse(
            "partials/import_result.html",
            {"request": request, "error": "Invalid file — could not parse XML.", "imported": 0, "skipped": 0},
        )

    feeds_to_import: list[tuple[str, str | None]] = []
    body_el = root.find("body") or root
    for child in body_el:
        if child.tag != "outline":
            continue
        xml_url = (child.get("xmlUrl") or child.get("xmlurl", "")).strip()
        if xml_url:
            feeds_to_import.append((xml_url, None))
        else:
            cat_name = (child.get("text") or child.get("title") or "").strip() or None
            for subchild in child:
                if subchild.tag != "outline":
                    continue
                sub_url = (subchild.get("xmlUrl") or subchild.get("xmlurl", "")).strip()
                if sub_url:
                    feeds_to_import.append((sub_url, cat_name))

    if not feeds_to_import:
        return templates.TemplateResponse(
            "partials/import_result.html",
            {"request": request, "error": "No feeds found in the uploaded file.", "imported": 0, "skipped": 0},
        )

    imported = 0
    skipped = 0
    for url, category in feeds_to_import:
        if db.query(Feed).filter_by(url=url).first():
            skipped += 1
            continue
        feed = Feed(url=url, category=category)
        db.add(feed)
        db.commit()
        db.refresh(feed)
        _import_executor.submit(fetch_feed, feed.id)
        schedule_feed(feed)
        imported += 1

    return templates.TemplateResponse(
        "partials/import_result.html",
        {"request": request, "error": None, "imported": imported, "skipped": skipped},
    )


# ---------------------------------------------------------------------------
# Feed management page
# ---------------------------------------------------------------------------

@app.get("/feeds", response_class=HTMLResponse)
def feeds_page(request: Request, db: Session = Depends(get_db)):
    feeds = db.query(Feed).order_by(Feed.title).all()
    return templates.TemplateResponse(
        "feeds.html",
        {"request": request, "feeds": feeds},
    )


@app.post("/feeds", response_class=HTMLResponse)
def add_feed(
    request: Request,
    url: str = Form(...),
    category: str | None = Form(None),
    db: Session = Depends(get_db),
    _: None = Depends(require_htmx),
):
    url = url.strip()
    error = None

    try:
        _assert_public_url(url)
    except ValueError as exc:
        error = f"Invalid feed URL: {exc}"

    if not error:
        existing = db.query(Feed).filter_by(url=url).first()
        if existing:
            error = "Feed already exists."

    if not error:
        feed = Feed(url=url, category=category.strip() or None if category else None)
        db.add(feed)
        db.commit()
        db.refresh(feed)
        # Fetch immediately in a thread so the user sees articles fast
        threading.Thread(target=fetch_feed, args=(feed.id,), daemon=True).start()
        schedule_feed(feed)

    feeds = db.query(Feed).order_by(Feed.title).all()
    return templates.TemplateResponse(
        "partials/feed_list.html",
        {"request": request, "feeds": feeds, "error": error},
    )


@app.delete("/feeds/{feed_id}", response_class=HTMLResponse)
def delete_feed(
    feed_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_htmx),
):
    feed = db.get(Feed, feed_id)
    if feed:
        unschedule_feed(feed_id)
        db.delete(feed)
        db.commit()
    feeds = db.query(Feed).order_by(Feed.title).all()
    return templates.TemplateResponse(
        "partials/feed_list.html",
        {"request": request, "feeds": feeds},
    )


@app.patch("/feeds/{feed_id}", response_class=HTMLResponse)
def update_feed(
    feed_id: int,
    request: Request,
    fetch_interval_min: int | None = Form(None),
    category: str | None = Form(None),
    db: Session = Depends(get_db),
    _: None = Depends(require_htmx),
):
    feed = db.get(Feed, feed_id)
    if feed is None:
        return Response(status_code=404)
    if fetch_interval_min is not None:
        feed.fetch_interval_min = max(1, min(1440, fetch_interval_min))
    if category is not None:
        feed.category = category.strip() or None
    db.commit()
    if fetch_interval_min is not None:
        schedule_feed(feed)
    feeds = db.query(Feed).order_by(Feed.title).all()
    return templates.TemplateResponse(
        "partials/feed_list.html",
        {"request": request, "feeds": feeds},
    )


@app.post("/feeds/refresh-all", response_class=HTMLResponse)
def refresh_all_feeds(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_htmx),
):
    feeds = db.query(Feed).order_by(Feed.title).all()
    for feed in feeds:
        threading.Thread(target=fetch_feed, args=(feed.id,), daemon=True).start()
    return templates.TemplateResponse(
        "partials/feed_list.html",
        {"request": request, "feeds": feeds},
    )


@app.post("/feeds/{feed_id}/refresh", response_class=HTMLResponse)
def refresh_feed(
    feed_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_htmx),
):
    threading.Thread(target=fetch_feed, args=(feed_id,), daemon=True).start()
    feeds = db.query(Feed).order_by(Feed.title).all()
    return templates.TemplateResponse(
        "partials/feed_list.html",
        {"request": request, "feeds": feeds},
    )
