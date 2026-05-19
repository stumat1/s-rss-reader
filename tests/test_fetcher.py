from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.fetcher import _assert_public_url, _parse_dt, _safe_url


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
