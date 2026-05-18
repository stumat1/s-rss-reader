import logging
import os
import threading
import xml.etree.ElementTree as ET
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import func, text
from fastapi import Depends, FastAPI, File, Form, Request, Response, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import Base, engine, get_db
from app.fetcher import fetch_feed, schedule_feed, start_scheduler, unschedule_feed
from app.models import Article, Feed

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

PAGE_SIZE = 50

BASE_DIR = Path(__file__).parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE articles ADD COLUMN is_read BOOLEAN NOT NULL DEFAULT 0"))
            conn.commit()
        except Exception:
            pass  # column already exists
    start_scheduler()
    yield


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR.parent / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


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
):
    article = db.get(Article, article_id)
    if article is None:
        return Response(status_code=404)
    article.is_favourite = not article.is_favourite
    db.commit()
    return templates.TemplateResponse(
        "partials/article_card.html",
        {"request": request, "article": article},
    )


@app.patch("/articles/read-all")
def mark_all_read(
    feed_id: int | None = None,
    favourites: int = 0,
    db: Session = Depends(get_db),
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
def delete_article(article_id: int, db: Session = Depends(get_db)):
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

    for feed in feeds:
        attrs = {
            "type": "rss",
            "text": feed.title or feed.url,
            "title": feed.title or feed.url,
            "xmlUrl": feed.url,
        }
        if feed.site_url:
            attrs["htmlUrl"] = feed.site_url
        ET.SubElement(body, "outline", **attrs)

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
):
    try:
        content = file.file.read()
        root = ET.fromstring(content)
    except ET.ParseError:
        return templates.TemplateResponse(
            "partials/import_result.html",
            {"request": request, "error": "Invalid file — could not parse XML.", "imported": 0, "skipped": 0},
        )

    urls = [
        (outline.get("xmlUrl") or outline.get("xmlurl", "")).strip()
        for outline in root.iter("outline")
        if outline.get("xmlUrl") or outline.get("xmlurl")
    ]

    if not urls:
        return templates.TemplateResponse(
            "partials/import_result.html",
            {"request": request, "error": "No feeds found in the uploaded file.", "imported": 0, "skipped": 0},
        )

    imported = 0
    skipped = 0
    for url in urls:
        if db.query(Feed).filter_by(url=url).first():
            skipped += 1
            continue
        feed = Feed(url=url)
        db.add(feed)
        db.commit()
        db.refresh(feed)
        threading.Thread(target=fetch_feed, args=(feed.id,), daemon=True).start()
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
    db: Session = Depends(get_db),
):
    url = url.strip()
    error = None

    existing = db.query(Feed).filter_by(url=url).first()
    if existing:
        error = "Feed already exists."
    else:
        feed = Feed(url=url)
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


@app.post("/feeds/{feed_id}/refresh", response_class=HTMLResponse)
def refresh_feed(
    feed_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    threading.Thread(target=fetch_feed, args=(feed_id,), daemon=True).start()
    feeds = db.query(Feed).order_by(Feed.title).all()
    return templates.TemplateResponse(
        "partials/feed_list.html",
        {"request": request, "feeds": feeds},
    )
