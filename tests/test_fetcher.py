import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from app.fetcher import _assert_public_url, _http_get, _parse_dt, _safe_url


class TestAssertPublicUrl:
    def test_accepts_public_ip(self):
        _assert_public_url("http://8.8.8.8/feed")
        _assert_public_url("https://8.8.8.8/feed")

    def test_rejects_loopback_ip(self):
        with pytest.raises(ValueError):
            _assert_public_url("http://127.0.0.1/feed")

    def test_rejects_private_ip(self):
        for url in (
            "http://10.0.0.1/feed",
            "http://192.168.1.1/feed",
            "http://172.16.0.1/feed",
        ):
            with pytest.raises(ValueError):
                _assert_public_url(url)

    def test_rejects_link_local(self):
        # AWS metadata endpoint
        with pytest.raises(ValueError):
            _assert_public_url("http://169.254.169.254/latest/meta-data")

    def test_rejects_non_http_scheme(self):
        for url in (
            "file:///etc/passwd",
            "javascript:alert(1)",
            "ftp://example.com/feed",
            "data:text/html,<script>",
        ):
            with pytest.raises(ValueError):
                _assert_public_url(url)


class TestSafeUrl:
    def test_accepts_http_and_https(self):
        assert _safe_url("http://example.com") == "http://example.com"
        assert _safe_url("https://example.com/x") == "https://example.com/x"

    def test_rejects_dangerous_schemes(self):
        assert _safe_url("javascript:alert(1)") is None
        assert _safe_url("data:text/html,<script>") is None
        assert _safe_url("file:///etc/passwd") is None
        assert _safe_url("vbscript:msgbox") is None

    def test_handles_none_and_empty(self):
        assert _safe_url(None) is None
        assert _safe_url("") is None


class TestParseDt:
    def test_parses_published_parsed(self):
        entry = SimpleNamespace(
            published_parsed=(2026, 1, 15, 12, 30, 45, 0, 0, 0),
            updated_parsed=None,
        )
        result = _parse_dt(entry)
        assert result == datetime(2026, 1, 15, 12, 30, 45, tzinfo=timezone.utc)

    def test_falls_back_to_updated_parsed(self):
        entry = SimpleNamespace(
            published_parsed=None,
            updated_parsed=(2026, 2, 20, 8, 0, 0, 0, 0, 0),
        )
        result = _parse_dt(entry)
        assert result == datetime(2026, 2, 20, 8, 0, 0, tzinfo=timezone.utc)

    def test_returns_none_when_neither_present(self):
        entry = SimpleNamespace(published_parsed=None, updated_parsed=None)
        assert _parse_dt(entry) is None

    def test_returns_none_when_no_attrs(self):
        # An entry with no relevant fields at all (getattr default)
        entry = SimpleNamespace()
        assert _parse_dt(entry) is None


class TestHttpGet304:
    """urllib raises HTTPError for non-2xx, including 304 Not Modified —
    the normal reply to a conditional GET. _http_get must surface it as a
    response, not let it blow up the fetch."""

    def test_returns_304_without_raising(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(304)
                self.end_headers()

            def log_message(self, *args):
                pass

        srv = HTTPServer(("127.0.0.1", 0), Handler)
        port = srv.server_address[1]
        threading.Thread(target=srv.handle_request, daemon=True).start()
        try:
            body, etag, last_modified, status = _http_get(
                f"http://127.0.0.1:{port}/", '"abc"', None
            )
        finally:
            srv.server_close()

        assert status == 304
        assert body == b""


class TestSweepStaleFeeds:
    """The fallback sweep used to crash with TypeError because SQLite returns
    naive datetimes that can't be subtracted from an aware `now`."""

    def test_handles_naive_last_fetched_at(self, monkeypatch):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from app import fetcher
        from app.database import Base
        from app.models import Feed

        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        TestSession = sessionmaker(bind=engine)

        s = TestSession()
        # Naive timestamp, far in the past → would be "stale".
        s.add(Feed(url="http://example.com/feed",
                   last_fetched_at=datetime(2000, 1, 1, 0, 0, 0),
                   fetch_interval_min=30))
        s.commit()
        s.close()

        monkeypatch.setattr(fetcher, "SessionLocal", TestSession)
        # Pretend a live job already exists so the sweep doesn't trigger a
        # real network fetch — we only care that the date math survives.
        monkeypatch.setattr(fetcher.scheduler, "get_job", lambda job_id: object())

        fetcher._sweep_stale_feeds()  # must not raise
