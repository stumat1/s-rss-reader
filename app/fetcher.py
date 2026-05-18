import logging
import os
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import bleach
import feedparser
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Article, Feed

log = logging.getLogger(__name__)

MAX_ARTICLES = int(os.environ.get("MAX_ARTICLES_PER_FEED", 200))
DEFAULT_INTERVAL = int(os.environ.get("FETCH_INTERVAL_MIN", 30))

scheduler = BackgroundScheduler()

ALLOWED_TAGS = [
    "a", "b", "blockquote", "br", "code", "em", "i",
    "li", "ol", "p", "pre", "s", "strong", "ul",
]


def _safe_attrs(tag: str, name: str, value: str) -> bool:
    if tag == "a":
        if name == "href":
            return not value.strip().lower().startswith(("javascript:", "data:", "vbscript:"))
        return name == "title"
    return False


def _parse_dt(entry) -> datetime | None:
    for field in ("published_parsed", "updated_parsed"):
        val = getattr(entry, field, None)
        if val:
            try:
                import time
                return datetime(*val[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return None


def _favicon(site_url: str | None) -> str | None:
    if not site_url:
        return None
    parsed = urlparse(site_url)
    return f"{parsed.scheme}://{parsed.netloc}/favicon.ico"


def fetch_feed(feed_id: int) -> None:
    db: Session = SessionLocal()
    try:
        feed = db.get(Feed, feed_id)
        if feed is None:
            return

        parsed = feedparser.parse(feed.url)

        if parsed.bozo and not parsed.entries:
            feed.error = str(parsed.bozo_exception)
            db.commit()
            return

        feed.error = None
        feed.last_fetched_at = datetime.now(timezone.utc)

        if not feed.title:
            feed.title = parsed.feed.get("title") or feed.url
        if not feed.site_url:
            feed.site_url = parsed.feed.get("link")
        if not feed.favicon_url:
            feed.favicon_url = _favicon(feed.site_url)

        new_count = 0
        update_count = 0
        for entry in parsed.entries:
            guid = entry.get("id") or entry.get("link") or entry.get("title", "")
            if not guid:
                continue

            summary_raw = (
                entry.get("summary")
                or (entry.content[0].value if entry.get("content") else None)
                or ""
            )
            summary = bleach.clean(summary_raw, tags=ALLOWED_TAGS, attributes=_safe_attrs, strip=True).strip()
            summary = summary[:2000] if summary else None
            new_title = entry.get("title")

            existing = (
                db.query(Article)
                .filter_by(feed_id=feed_id, guid=guid)
                .first()
            )
            if existing:
                if existing.title != new_title or existing.summary != summary:
                    existing.title = new_title
                    existing.summary = summary
                    update_count += 1
                continue

            article = Article(
                feed_id=feed_id,
                guid=guid,
                title=new_title,
                link=entry.get("link"),
                summary=summary,
                published_at=_parse_dt(entry),
            )
            db.add(article)
            new_count += 1

        db.commit()

        # Prune oldest articles beyond cap (keep favourites)
        total = (
            db.query(Article)
            .filter_by(feed_id=feed_id, is_deleted=False, is_favourite=False)
            .count()
        )
        if total > MAX_ARTICLES:
            overflow = total - MAX_ARTICLES
            oldest = (
                db.query(Article)
                .filter_by(feed_id=feed_id, is_deleted=False, is_favourite=False)
                .order_by(Article.published_at.asc().nullsfirst(), Article.fetched_at.asc())
                .limit(overflow)
                .all()
            )
            for a in oldest:
                a.is_deleted = True
            db.commit()

        if new_count or update_count:
            log.info(
                "Feed %s: %d new, %d updated",
                feed.title or feed.url,
                new_count,
                update_count,
            )

    except Exception as exc:
        log.exception("Error fetching feed %d: %s", feed_id, exc)
        try:
            feed = db.get(Feed, feed_id)
            if feed:
                feed.error = str(exc)
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


def _job_id(feed_id: int) -> str:
    return f"feed_{feed_id}"


def schedule_feed(feed: Feed) -> None:
    interval = feed.fetch_interval_min or DEFAULT_INTERVAL
    job_id = _job_id(feed.id)
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    scheduler.add_job(
        fetch_feed,
        "interval",
        minutes=interval,
        id=job_id,
        args=[feed.id],
        replace_existing=True,
    )


def unschedule_feed(feed_id: int) -> None:
    job_id = _job_id(feed_id)
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)


def start_scheduler() -> None:
    db: Session = SessionLocal()
    try:
        feeds = db.query(Feed).all()
        for feed in feeds:
            schedule_feed(feed)
    finally:
        db.close()

    # Fallback sweep every 30 min for any missed feeds
    scheduler.add_job(
        _sweep_stale_feeds,
        "interval",
        minutes=30,
        id="sweep",
        replace_existing=True,
    )
    scheduler.start()
    log.info("Scheduler started with %d feed(s)", len(feeds))


def _sweep_stale_feeds() -> None:
    from datetime import timedelta
    db: Session = SessionLocal()
    try:
        feeds = db.query(Feed).all()
        now = datetime.now(timezone.utc)
        for feed in feeds:
            if feed.last_fetched_at is None or (
                now - feed.last_fetched_at
            ) > timedelta(minutes=feed.fetch_interval_min or DEFAULT_INTERVAL):
                if not scheduler.get_job(_job_id(feed.id)):
                    fetch_feed(feed.id)
    finally:
        db.close()
