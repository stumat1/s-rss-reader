import colorsys
import hashlib
import logging
import os
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import func, or_, text
from sqlalchemy.exc import IntegrityError, OperationalError
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import Base, SessionLocal, engine, get_db
from app.fetcher import fetch_feed, get_max_articles, schedule_feed, scheduler, set_max_articles, start_scheduler, unschedule_feed, _assert_public_url
from app.models import Article, Category, Feed, Setting

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
            "ALTER TABLE feeds ADD COLUMN consecutive_failures INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE categories ADD COLUMN color TEXT",
            "CREATE INDEX IF NOT EXISTS ix_articles_feed_deleted_published ON articles (feed_id, is_deleted, published_at)",
            "CREATE INDEX IF NOT EXISTS ix_articles_deleted_read ON articles (is_deleted, is_read)",
            "CREATE INDEX IF NOT EXISTS ix_articles_deleted_favourite ON articles (is_deleted, is_favourite)",
        ]:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except OperationalError as exc:
                # Most often: column/index already exists. Log so a real
                # failure (disk full, permissions) is still visible at DEBUG.
                log.debug("Skipping migration step %r: %s", stmt, exc)
    with SessionLocal() as db:
        s = db.get(Setting, "max_articles_per_feed")
        if s:
            set_max_articles(int(s.value))
    start_scheduler()
    yield
    scheduler.shutdown(wait=False)
    _import_executor.shutdown(wait=False)


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR.parent / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _hue_to_hex(hue: int) -> str:
    # colorsys uses HLS (note the order): hue [0,1], lightness, saturation.
    r, g, b = colorsys.hls_to_rgb(hue / 360.0, 0.55, 0.65)
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


def category_color(name: str | None, override: str | None = None) -> str:
    """Resolve a category color. Returns an explicit override if set, else a
    deterministic hash-derived hex. Same name → same fallback color across reloads."""
    if override and _HEX_COLOR_RE.match(override):
        return override
    if not name:
        return "#999999"
    hue = int(hashlib.sha256(name.encode("utf-8")).hexdigest(), 16) % 360
    return _hue_to_hex(hue)


templates.env.filters["category_color"] = category_color


def require_htmx(request: Request) -> None:
    """Dependency: reject mutation requests that didn't come from HTMX (CSRF guard)."""
    if request.headers.get("HX-Request") != "true":
        raise HTTPException(status_code=403, detail="Direct form submission not allowed")


@app.get("/health")
def health():
    return {"ok": True}


# ---------------------------------------------------------------------------
# Article river
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    feed_id: int | None = None,
    favourites: int = 0,
    unread: int = 0,
    q: str | None = None,
    db: Session = Depends(get_db),
):
    feeds = db.query(Feed).order_by(Feed.title).all()
    articles = _query_articles(db, feed_id=feed_id, favourites=bool(favourites), unread=bool(unread), q=q)
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
            "search_q": q or "",
            "unread_counts": unread_counts,
            "total_unread": total_unread,
            "total_favourites": total_favourites,
            "category_colors": _category_colors(db),
            "page_size": PAGE_SIZE,
        },
    )


@app.get("/articles", response_class=HTMLResponse)
def articles_partial(
    request: Request,
    feed_id: int | None = None,
    favourites: int = 0,
    unread: int = 0,
    q: str | None = None,
    db: Session = Depends(get_db),
):
    articles = _query_articles(db, feed_id=feed_id, favourites=bool(favourites), unread=bool(unread), q=q)
    return templates.TemplateResponse(
        "partials/article_list.html",
        {
            "request": request,
            "articles": articles,
            "active_feed_id": feed_id,
            "show_favourites": bool(favourites),
            "show_unread": bool(unread),
            "search_q": q or "",
            "page_size": PAGE_SIZE,
        },
    )


@app.get("/articles/more", response_class=HTMLResponse)
def articles_more(
    request: Request,
    feed_id: int | None = None,
    favourites: int = 0,
    unread: int = 0,
    q: str | None = None,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    articles = _query_articles(
        db,
        feed_id=feed_id,
        favourites=bool(favourites),
        unread=bool(unread),
        q=q,
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
            "search_q": q or "",
            "offset": offset,
            "page_size": PAGE_SIZE,
        },
    )


def _query_articles(
    db: Session,
    feed_id: int | None = None,
    favourites: bool = False,
    unread: bool = False,
    q: str | None = None,
    offset: int = 0,
):
    query = (
        db.query(Article)
        .join(Feed)
        .filter(Article.is_deleted == False)  # noqa: E712
    )
    if feed_id:
        query = query.filter(Article.feed_id == feed_id)
    if favourites:
        query = query.filter(Article.is_favourite == True)  # noqa: E712
    if unread:
        query = query.filter(Article.is_read == False)  # noqa: E712
    if q and q.strip():
        # Escape LIKE wildcards so a literal % or _ in user input doesn't match anything.
        term = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{term}%"
        query = query.filter(
            or_(
                Article.title.ilike(pattern, escape="\\"),
                Article.summary.ilike(pattern, escape="\\"),
            )
        )
    return (
        query.order_by(
            Article.published_at.desc().nullslast(),
            Article.fetched_at.desc(),
            Article.id.desc(),
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
    q: str | None = None,
    db: Session = Depends(get_db),
    _: None = Depends(require_htmx),
):
    query = db.query(Article).filter(
        Article.is_deleted == False,  # noqa: E712
        Article.is_read == False,  # noqa: E712
    )
    if feed_id:
        query = query.filter(Article.feed_id == feed_id)
    if favourites:
        query = query.filter(Article.is_favourite == True)  # noqa: E712
    if q and q.strip():
        term = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{term}%"
        query = query.filter(
            or_(
                Article.title.ilike(pattern, escape="\\"),
                Article.summary.ilike(pattern, escape="\\"),
            )
        )
    query.update({"is_read": True}, synchronize_session=False)
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


@app.delete("/articles")
def delete_all_articles(
    feed_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_htmx),
):
    feed = db.get(Feed, feed_id)
    if feed is None:
        return Response(status_code=404)
    db.query(Article).filter(
        Article.feed_id == feed_id,
        Article.is_deleted == False,  # noqa: E712
    ).update({"is_deleted": True}, synchronize_session=False)
    db.commit()
    return Response(status_code=200, headers={"HX-Refresh": "true"})


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
    return templates.TemplateResponse(
        "settings.html",
        {"request": request, "max_articles": get_max_articles()},
    )


@app.post("/settings/max-articles", response_class=HTMLResponse)
def save_max_articles(
    request: Request,
    max_articles_per_feed: int = Form(...),
    db: Session = Depends(get_db),
    _: None = Depends(require_htmx),
):
    val = max(1, min(10000, max_articles_per_feed))
    setting = db.get(Setting, "max_articles_per_feed")
    if setting:
        setting.value = str(val)
    else:
        db.add(Setting(key="max_articles_per_feed", value=str(val)))
    db.commit()
    set_max_articles(val)
    return HTMLResponse(
        f'<div class="import-result import-result--ok">Saved — keeping up to {val} articles per feed.</div>'
    )


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
        try:
            _assert_public_url(url)
        except ValueError:
            skipped += 1
            continue
        if db.query(Feed).filter_by(url=url).first():
            skipped += 1
            continue
        feed = Feed(url=url, category=category)
        db.add(feed)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            skipped += 1
            continue
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

def _categories_with_counts(db: Session) -> list[tuple[str, int, str | None]]:
    """Return (name, feed_count, explicit_color) for every category."""
    from collections import Counter
    feed_cats = [
        c for (c,) in db.query(Feed.category)
        .filter(Feed.category.isnot(None), Feed.category != "")
        .all()
    ]
    registered = {c.name: c.color for c in db.query(Category).all()}
    all_names = sorted(set(feed_cats) | set(registered.keys()))
    counts = Counter(feed_cats)
    return [(n, counts.get(n, 0), registered.get(n)) for n in all_names]


def _category_colors(db: Session) -> dict[str, str]:
    """Map of category name → explicit hex color for those with one set."""
    return {
        c.name: c.color
        for c in db.query(Category).filter(Category.color.isnot(None)).all()
    }


def _feed_list_context(request: Request, db: Session, error: str | None = None) -> dict:
    return {
        "request": request,
        "feeds": db.query(Feed).order_by(Feed.title).all(),
        "categories_with_counts": _categories_with_counts(db),
        "category_colors": _category_colors(db),
        "error": error,
    }

@app.get("/feeds", response_class=HTMLResponse)
def feeds_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("feeds.html", _feed_list_context(request, db))


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
        clean_category = (category or "").strip() or None
        feed = Feed(url=url, category=clean_category)
        db.add(feed)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            error = "Feed already exists."
        else:
            db.refresh(feed)
            # Fetch immediately in a worker so the user sees articles fast
            _import_executor.submit(fetch_feed, feed.id)
            schedule_feed(feed)

    return templates.TemplateResponse(
        "partials/feed_list.html", _feed_list_context(request, db, error=error)
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
    return templates.TemplateResponse(
        "partials/feed_list.html", _feed_list_context(request, db)
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
    return templates.TemplateResponse(
        "partials/feed_list.html", _feed_list_context(request, db)
    )


@app.post("/categories", response_class=HTMLResponse)
def create_category(
    request: Request,
    name: str = Form(...),
    db: Session = Depends(get_db),
    _: None = Depends(require_htmx),
):
    cleaned = name.strip()
    if cleaned and db.get(Category, cleaned) is None:
        db.add(Category(name=cleaned))
        db.commit()
    return templates.TemplateResponse(
        "partials/feed_list.html", _feed_list_context(request, db)
    )


@app.post("/categories/color", response_class=HTMLResponse)
def set_category_color(
    request: Request,
    name: str = Form(...),
    color: str = Form(...),
    db: Session = Depends(get_db),
    _: None = Depends(require_htmx),
):
    cleaned = name.strip()
    if cleaned and _HEX_COLOR_RE.match(color):
        entry = db.get(Category, cleaned)
        if entry is None:
            entry = Category(name=cleaned, color=color)
            db.add(entry)
        else:
            entry.color = color
        db.commit()
    return templates.TemplateResponse(
        "partials/feed_list.html", _feed_list_context(request, db)
    )


@app.delete("/categories/{name}", response_class=HTMLResponse)
def delete_category(
    name: str,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_htmx),
):
    in_use = db.query(Feed).filter(Feed.category == name).count()
    if in_use:
        return templates.TemplateResponse(
            "partials/feed_list.html",
            _feed_list_context(request, db, error=f"Cannot delete '{name}': {in_use} feed(s) still use it."),
        )
    entry = db.get(Category, name)
    if entry:
        db.delete(entry)
        db.commit()
    return templates.TemplateResponse(
        "partials/feed_list.html", _feed_list_context(request, db)
    )


@app.post("/categories/rename", response_class=HTMLResponse)
def rename_category(
    request: Request,
    old: str = Form(...),
    new: str = Form(...),
    db: Session = Depends(get_db),
    _: None = Depends(require_htmx),
):
    old = old.strip()
    new_clean = new.strip() or None
    if old and new_clean != old:
        db.query(Feed).filter(Feed.category == old).update(
            {"category": new_clean}, synchronize_session=False
        )
        # Keep the registry in sync: carry forward the color when renaming.
        old_entry = db.get(Category, old)
        old_color = old_entry.color if old_entry else None
        if old_entry:
            db.delete(old_entry)
        if new_clean:
            new_entry = db.get(Category, new_clean)
            if new_entry is None:
                db.add(Category(name=new_clean, color=old_color))
            elif old_color and not new_entry.color:
                new_entry.color = old_color
        db.commit()
    return templates.TemplateResponse(
        "partials/feed_list.html", _feed_list_context(request, db)
    )


@app.post("/feeds/refresh-all", response_class=HTMLResponse)
def refresh_all_feeds(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_htmx),
):
    feeds = db.query(Feed).order_by(Feed.title).all()
    for feed in feeds:
        _import_executor.submit(fetch_feed, feed.id)
    return templates.TemplateResponse(
        "partials/feed_list.html", _feed_list_context(request, db)
    )


@app.post("/feeds/{feed_id}/refresh", response_class=HTMLResponse)
def refresh_feed(
    feed_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_htmx),
):
    _import_executor.submit(fetch_feed, feed_id)
    return templates.TemplateResponse(
        "partials/feed_list.html", _feed_list_context(request, db)
    )
