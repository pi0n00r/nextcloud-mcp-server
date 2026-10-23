"""Tests for _safe_next_url, the open-redirect guard for ``?next=`` params.

Pins the contract that any non-path target falls back to the default,
preventing the open-redirect issue flagged on PR #758.
"""

import pytest

from nextcloud_mcp_server.auth.browser_oauth_routes import _safe_next_url

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Valid path-only targets pass through.
        ("/app", "/app"),
        ("/app/foo", "/app/foo"),
        ("/oauth/login", "/oauth/login"),
        ("/app?x=1&y=2", "/app?x=1&y=2"),
        ("/app#frag", "/app#frag"),
        # Empty / missing → default.
        ("", "/default"),
        (None, "/default"),
        # Absolute URLs → default.
        ("https://evil.example.com", "/default"),
        ("http://evil.example.com/path", "/default"),
        # Protocol-relative → default. Browser would treat as cross-origin.
        ("//evil.example.com", "/default"),
        ("//evil.example.com/path", "/default"),
        # No leading slash → default.
        ("relative/path", "/default"),
        ("app", "/default"),
        # Whitespace / control chars → default. Defends against tab/space
        # injection that some browsers historically tolerated.
        ("/app\nfoo", "/default"),
        ("/app\tfoo", "/default"),
        ("/app\x00foo", "/default"),
        ("/app foo", "/default"),
    ],
)
def test_safe_next_url(raw, expected):
    assert _safe_next_url(raw, "/default") == expected
