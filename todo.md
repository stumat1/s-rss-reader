# RSS Reader — improvement backlog

A list to pick from, not a commitment. Constraint: keep the app simple — no SPA rewrite, no auth, no build step. Items marked ★ are quick wins. Sizing: S = under an hour, M = an evening, L = a weekend.

## Visual design

- ~~**General cosmetic pass (M/L)**~~ — done. [style.css](static/style.css) now derives accent-tinted shadows, hovers, focus rings, and a soft body glow from each theme's `--accent` via `color-mix()`, so all 10 themes pick up the polish. Article cards lift on hover, unread cards have a faint accent gradient, the brand has a glowing accent dot, and inputs/buttons share a single ring style.
- **Stretch: bundle a self-hosted Inter font (S)** — the font stack now prefers Inter but only renders it if installed locally. Self-hosting (woff2 in [static/](static/)) would give a consistent typographic feel across machines without a Google Fonts dependency.

## UX polish

- **★ Sticky list toolbar (S)** — the "Unread only / Mark all as read / Delete All" bar at the top of [article_list.html](app/templates/partials/article_list.html) scrolls out of view on long lists. Add `position: sticky; top: 0` so it's always reachable.
- **★ Unread count in browser tab title (S)** — `<title>` in [base.html](app/templates/base.html) is static. Prepend `(N) ` when there are unread articles so the tab is glanceable when backgrounded. Update on HTMX swap.
- **★ `/` keyboard shortcut to focus the search box (S)** — common pattern, fits naturally alongside the existing j/k/g/G shortcuts in [shortcuts.js](static/shortcuts.js). Add to the `?` help overlay.
- **Click anywhere on card to open + auto-mark-read (S/M)** — currently only the title link opens, and you must separately click the read-dot. Make the whole card a click target that opens the link AND marks it read in one action. Big ergonomic win for river-style reading.
- **Copy article link button (S)** — small button next to favourite/read/delete in [article_card.html](app/templates/partials/article_card.html). Useful for sharing without round-tripping through the source site.
- **Date-group headings in the river (M)** — insert "Today / Yesterday / This week / Older" dividers between articles in [article_list.html](app/templates/partials/article_list.html). Helps orient when scrolling a busy feed.
- **Mobile-friendly sidebar (M)** — sidebar is fixed-width in [style.css](static/style.css); on phones it dominates the viewport. Add a media query that collapses it behind a hamburger toggle below ~700px.

## Feed-management visibility

- **★ Show "last fetched" + error inline in sidebar (S)** — `last_fetched_at`, `error`, and `consecutive_failures` already exist in [models.py](app/models.py) but only surface on the `/feeds` page. Add a hover tooltip or subtle ⚠ icon in [index.html](app/templates/index.html)'s `feed_link` macro for feeds that haven't fetched in >24h or have errors. Don't make it noisy — just enough to notice rot.
- **Per-feed unread-only link (S)** — clicking a feed in the sidebar shows all its articles. Add a tiny "(unread)" affordance or shift-click behaviour to filter to unread within that feed.
- **Refresh-all without full page reload (S)** — [index.html:117](app/templates/index.html#L117) does `window.location.reload()` after refresh-all. Swap just `#articles` + sidebar badges instead — feels instant, avoids scroll jump.
- **Toast/flash messages for background actions (M)** — "Refreshed 12 feeds, 3 new articles", "Feed X failed to fetch", "Pruned 50 old articles". Small dismissible banner area in [base.html](app/templates/base.html), updated via HTMX out-of-band swaps. The app currently does all of these silently.

## Reading workflow

- **Auto-mark-read on scroll past (M)** — Inoreader-style: as a card scrolls above the viewport, mark it read. Behind a settings toggle (some people hate it). Heavier than other items because of debouncing/batching the PATCH calls.
- **Show full feed item content when available (M)** — `summary` may be truncated. Many feeds include the full article in `content:encoded`; capture it in [fetcher.py](app/fetcher.py) and show on click-to-expand. Avoids the new-tab dance for feeds that publish full text.
- **"Mark all read" — undoable for 5 seconds (S)** — easy to hit accidentally on a 200-article feed. Wrap with a toast like "Marked 47 as read. [Undo]". Stash the IDs client-side, PATCH them back on undo.

## Power-user nice-to-haves (lower priority)

- **Backup / restore button on /feeds (S)** — single button that downloads the SQLite file. Removes the "where is my data" question for self-hosters. OPML export already covers feed list, but not articles/favourites.
- **Per-feed stats card (M)** — on the `/feeds` page, show "X articles, Y unread, last new article Z days ago" for each feed. Helps decide what to prune.
- **Read-later flag (M)** — distinct from favourites; favourites = "keep forever", read-later = "I'll get to it soon". Adds a column + filter. Only worth it if you actually feel the gap — favourites may be doing double duty fine.
