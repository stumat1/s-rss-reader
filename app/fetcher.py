import ipaddress
import logging
import os
import socket
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import feedparser
import nh3
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Article, Feed

log = logging.getLogger(__name__)

MAX_ARTICLES = int(os.environ.get("MAX_ARTICLES_PER_FEED", 200))
DEFAULT_INTERVAL = int(os.environ.get("FETCH_INTERVAL_MIN", 30))

scheduler = BackgroundScheduler()

_ALLOWED_TAGS = {
    "a", "b", "blockquote", "br", "code", "em", "i",
    "li", "ol", "p", "pre", "s", "strong", "ul",
}

# nh3 strips javascript:/data:/vbscript: hrefs automatically; we only allow
# href and title on <a> and no attributes on any other tag.
_ALLOWED_ATTRS: dict[str, set[str]] = {
    "a": {"href", "title"},
}


def _safe_url(url: str | None) -> str | None:
    """Return url only if it uses http or https; discard javascript:, data:, file:// etc."""
    if not url:
        return None
    scheme = urlparse(url).scheme.lower()
    return url if scheme in ("http", "https") else None


def _assert_public_url(url: str) -> None:
    """Raise ValueError if url targets a private/loopback/non-routable address or non-http scheme."""
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError(f"Disallowed scheme: {parsed.scheme!r}")
    host = parsed.hostname or ""
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        # hostname — resolve it and check every returned address
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror:
            return  # unresolvable; let feedparser / urllib surface the error naturally
        for *_, sockaddr in infos:
            try:
                addr = ipaddress.ip_address(sockaddr[0])
                if not addr.is_global:
                    raise ValueError(f"Disallowed address for {host!r}: {addr}")
            except ValueError as exc:
                if "Disallowed" in str(exc):
                    raise
        return
    if not addr.is_global:
        raise ValueError(f"Disallowed address: {addr}")



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


class _IconParser(HTMLParser):
    """Collects <link rel="icon"> hrefs from a page's <head>."""

    def __init__(self) -> None:
        super().__init__()
        self.icons: list[str] = []
        self._past_head = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if self._past_head:
            return
        if tag == "body":
            self._past_head = True
            return
        if tag == "link":
            d = dict(attrs)
            if "icon" in d.get("rel", "").lower().split():
                href = d.get("href", "").strip()
                if href and not href.startswith("data:"):
                    self.icons.append(href)

    def handle_endtag(self, tag: str) -> None:
        if tag == "head":
            self._past_head = True


def _resolve_favicon(site_url: str | None) -> str | None:
    if not site_url:
        return None
    try:
        _assert_public_url(site_url)
    except ValueError:
        return None
    parsed = urlparse(site_url)
    fallback = f"{parsed.scheme}://{parsed.netloc}/favicon.ico"
    try:
        req = urllib.request.Request(
            site_url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; rss-reader/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if "html" not in content_type:
                return fallback
            html = resp.read(32768).decode("utf-8", errors="replace")
        parser = _IconParser()
        parser.feed(html)
        if parser.icons:
            # Prefer raster/vector formats over .ico
            for href in parser.icons:
                if any(href.lower().endswith(ext) for ext in (".png", ".svg", ".webp", ".jpg", ".gif")):
                    return urljoin(site_url, href)
            return urljoin(site_url, parser.icons[0])
    except Exception:
        pass
    return fallback


def fetch_feed(feed_id: int) -> None:
    db: Session = SessionLocal()
    try:
        feed = db.get(Feed, feed_id)
        if feed is None:
            return

        try:
            _assert_public_url(feed.url)
        except ValueError as exc:
            feed.error = str(exc)
            db.commit()
            return

        parsed = feedparser.parse(
            feed.url,
            etag=feed.etag,
            modified=feed.last_modified,
        )

        if getattr(parsed, "status", None) == 304:
            feed.last_fetched_at = datetime.now(timezone.utc)
            db.commit()
            log.debug("Feed %s: not modified (304)", feed.title or feed.url)
            return

        if parsed.bozo and not parsed.entries:
            log.warning("Feed %d bozo error: %s", feed_id, parsed.bozo_exception)
            feed.error = "Could not parse feed"
            db.commit()
            return

        if parsed.bozo:
            log.warning("Feed %d parsed with errors (bozo), processing %d entries anyway: %s",
                        feed_id, len(parsed.entries), parsed.bozo_exception)

        feed.error = None
        feed.last_fetched_at = datetime.now(timezone.utc)
        if parsed.get("etag"):
            feed.etag = parsed.etag
        if parsed.get("modified"):
            feed.last_modified = parsed.modified

        if not feed.title:
            feed.title = parsed.feed.get("title") or feed.url
        if not feed.site_url:
            feed.site_url = parsed.feed.get("link")
        if not feed.favicon_url:
            feed.favicon_url = _resolve_favicon(feed.site_url)

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
            summary = nh3.clean(summary_raw, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRS).strip()
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
                link=_safe_url(entry.get("link")),
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
                feed.error = "Fetch failed — check server logs"
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
    # Hard-delete soft-deleted articles older than 30 days, run daily
    scheduler.add_job(
        _vacuum_deleted_articles,
        "interval",
        hours=24,
        id="vacuum",
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


def _vacuum_deleted_articles() -> None:
    from datetime import timedelta
    db: Session = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        deleted = (
            db.query(Article)
            .filter(Article.is_deleted == True, Article.fetched_at < cutoff)
            .delete(synchronize_session=False)
        )
        db.commit()
        if deleted:
            log.info("Vacuumed %d soft-deleted article(s)", deleted)
    except Exception as exc:
        log.exception("Error vacuuming deleted articles: %s", exc)
    finally:
        db.close()
