"""Tests for ``_normalise_origin`` port + scheme + host normalisation.

The CSRF guard on POST /oauth/logout (PR #758 round-3 review hardening)
compares ``Origin`` / ``Referer`` against the configured ``mcp_server_url``
via ``_normalise_origin``. RFC 6454 §6.2 says browsers omit default ports
(80 for http, 443 for https) from Origin headers, so the function strips
those before comparison. These tests pin that behaviour so it can't
silently regress.
"""

import pytest

from nextcloud_mcp_server.auth.browser_oauth_routes import _normalise_origin

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "left, right, equal",
    [
        # Default ports are stripped — these MUST compare equal.
        ("https://example.com", "https://example.com:443", True),
        ("https://example.com:443", "https://example.com", True),
        ("http://example.com", "http://example.com:80", True),
        ("http://example.com:80", "http://example.com", True),
        # Non-default ports are preserved.
        ("https://example.com:8443", "https://example.com", False),
        ("http://example.com:8080", "http://example.com", False),
        ("https://example.com:8443", "https://example.com:443", False),
        # Cross-scheme defaults don't collapse (https:443 != http:80 even
        # though both ports get stripped, because the scheme differs).
        ("https://example.com", "http://example.com", False),
        ("https://example.com:443", "http://example.com:80", False),
        # Hostname matters and is case-insensitive.
        ("https://example.com", "https://other.com", False),
        ("https://example.com", "https://EXAMPLE.COM", True),
        ("https://Example.Com:443", "https://example.com", True),
    ],
)
def test_normalise_origin_equivalence(left: str, right: str, equal: bool):
    assert (_normalise_origin(left) == _normalise_origin(right)) is equal
