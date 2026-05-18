# RSS Reader — Improvement Ideas

## UX / Reading experience

**1. Read/unread tracking** done
There is no `is_read` flag on `Article`. The only way to "clear" an article is to delete it, which is lossy. Adding `is_read: bool` (default `False`) would let users filter to unread, mark individual articles or whole feeds read, and keep the river clean without permanently discarding articles.

**2. Unread counts in sidebar** done
The sidebar lists feed names but gives no signal about pending items. A badge showing unread count per feed (and a total "All articles" count) would let users triage at a glance without opening each feed.

**3. Bulk "mark all read"** done
There is no way to dismiss all articles for a feed at once. A per-feed "Mark all read" action (HTMX PATCH to `/feeds/{id}/read`) would be the most-used bulk action.

**4. Hard article limit with no pagination** done
`_query_articles` is hard-capped at 300 rows with no pagination or infinite scroll. Users with many feeds hit a silent ceiling. Cursor-based pagination (e.g., `?before_id=`) or HTMX infinite scroll triggered at the bottom of the list would fix this without requiring a page reload.

**5. HTML stripped from summaries** done
`ALLOWED_TAGS = []` in `fetcher.py` strips all markup, so code blocks, links, and emphasis inside feed summaries are rendered as flat text. A conservative allowlist (e.g. `b, i, em, strong, code, pre, a, p, br`) would preserve readability while still blocking unsafe tags.

**6. Article content never updated on re-fetch** done
When a `guid` already exists, the fetcher silently skips it (`if exists: continue`). If a feed corrects a title or updates a summary after initial publication, the stored version stays stale. Consider updating `title` and `summary` on existing articles if the new values differ.

**7. Keyboard shortcuts** done
Standard RSS-reader shortcuts (`j`/`k` to move between articles, `o` to open the link, `f` to toggle favourite, `u` to mark read) are missing. These can be added with a small vanilla-JS snippet or HTMX extensions without breaking the no-build-step constraint.

---

## Feed management

**8. Per-feed fetch interval has no UI**
The `fetch_interval_min` column exists on `Feed` and is wired into the scheduler, but there is no UI to change it. Adding an inline edit field on the feeds page (HTMX PATCH to `/feeds/{id}`) would expose this already-implemented feature.

**9. Feed categories / folders**
OPML supports grouped outlines (`<outline text="Tech">` containing child `<outline>` items). The current import flattens everything. Adding an optional `category` field to `Feed` and grouping the sidebar by it would be a useful organisational step.

**10. Better favicon resolution**
`_favicon()` unconditionally guesses `{scheme}://{netloc}/favicon.ico`. Many sites serve icons at a different path declared in `<link rel="icon">`. Options: parse the site's HTML `<head>` on first fetch, or fall back to a third-party resolver (e.g. `https://www.google.com/s2/favicons?domain=…`) when the `.ico` guess 404s.

---

## Performance / reliability

**11. No conditional HTTP GET (ETag / Last-Modified)**
`feedparser` supports conditional GET — if you pass `etag` and `modified` from the previous response it sends `If-None-Match` / `If-Modified-Since` headers and the server can reply with HTTP 304. The app currently ignores both, so the full feed XML is re-downloaded on every poll even if nothing changed. Store `etag` and `last_modified` on `Feed` and pass them to `feedparser.parse()`.

**12. SQLite WAL mode not enabled**
The background scheduler writes to SQLite while the FastAPI sync routes also read/write. Without WAL mode, concurrent access causes `SQLITE_BUSY` errors under load. Adding `PRAGMA journal_mode=WAL` at engine startup (via a `@event.listens_for(engine, "connect")` hook) eliminates most contention.

**13. Thread-per-import in OPML bulk import**
`import_opml` spawns one daemon thread per feed in a loop (`threading.Thread(…).start()`). A large OPML file with 50+ feeds would spawn 50 threads at once. A `ThreadPoolExecutor` with a bounded worker count (e.g. 5) would be more controlled.

**14. Soft-deleted articles accumulate forever**
`is_deleted = True` rows are never hard-deleted. Over months these rows grow the database without providing any value (they are excluded from all queries). A periodic vacuum job (e.g. delete `is_deleted` rows older than 30 days) would keep the DB size in check.

---

## Minor bugs / polish

**15. Scheduler start log is broken**
In `start_scheduler()` the log line reads:

```python
log.info("Scheduler started with %d feed(s)", len(feeds) if 'feeds' in dir() else 0)
```

`dir()` returns local variable names in the current scope, not a reliable guard. `feeds` is always defined at that point in the function; the guard is dead code. Should just be `len(feeds)`.

**16. Manual refresh gives no feedback**
Clicking "Refresh" on a feed fires a background thread and immediately re-renders the feed list. There is no indication of whether the refresh succeeded or is still running. A transient "Fetching…" state on the button (using HTMX `hx-indicator`) would help.

**17. Dates displayed without timezone context**
`article.published_at.strftime("%b %d, %Y")` shows a bare date with no indicator that it is UTC. Showing a relative time (e.g. "2 hours ago") using the `<time>` element's `datetime` attribute and a tiny JS snippet (or `htmx.on`) would be more useful at a glance.

**18. `httpx` in requirements but unused**
`httpx==0.27.2` is listed in `requirements.txt` but never imported. `feedparser` uses `urllib` internally. Either remove `httpx` or adopt it in the fetcher for timeout control and connection pooling.
