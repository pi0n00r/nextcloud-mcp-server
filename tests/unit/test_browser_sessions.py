"""Unit tests for browser_sessions storage (issue #626 finding 2).

The browser admin UI no longer uses the raw user_id as the cookie value
— it uses a cryptographically random session_id mapped server-side to
user_id. These tests pin the storage contract.
"""

import secrets
import tempfile
import time
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from nextcloud_mcp_server.auth.storage import RefreshTokenStorage

pytestmark = pytest.mark.unit


@pytest.fixture
async def storage():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_browser_sessions.db"
        s = RefreshTokenStorage(
            db_path=str(db_path), encryption_key=Fernet.generate_key().decode()
        )
        await s.initialize()
        yield s


async def test_create_and_get_browser_session(storage):
    sid = secrets.token_urlsafe(32)
    await storage.create_browser_session(session_id=sid, user_id="alice")

    user_id = await storage.get_browser_session_user(sid)
    assert user_id == "alice"


async def test_get_browser_session_unknown_returns_none(storage):
    assert await storage.get_browser_session_user("does-not-exist") is None


async def test_delete_browser_session(storage):
    sid = secrets.token_urlsafe(32)
    await storage.create_browser_session(session_id=sid, user_id="alice")

    deleted = await storage.delete_browser_session(sid)
    assert deleted is True
    assert await storage.get_browser_session_user(sid) is None


async def test_expired_browser_session_rejected_and_deleted(storage):
    sid = secrets.token_urlsafe(32)
    # ttl_seconds=0 so the row is immediately expired (now == expires_at)
    await storage.create_browser_session(session_id=sid, user_id="alice", ttl_seconds=0)
    # Make sure clock advances past expires_at
    time.sleep(0.01)

    assert await storage.get_browser_session_user(sid) is None
    # Expired row should be deleted on encounter
    assert await storage.delete_browser_session(sid) is False


async def test_replace_existing_session_id(storage):
    """INSERT OR REPLACE so re-using a session_id rebinds the user.

    Not a recommended call pattern (session_ids are random), but the
    storage layer must not raise UNIQUE constraint errors if it happens.
    """
    sid = secrets.token_urlsafe(32)
    await storage.create_browser_session(session_id=sid, user_id="alice")
    await storage.create_browser_session(session_id=sid, user_id="bob")

    assert await storage.get_browser_session_user(sid) == "bob"


async def test_cleanup_expired_browser_sessions(storage):
    """Periodic cleanup removes expired rows but leaves fresh ones (PR #758 finding 6)."""
    fresh_sid = secrets.token_urlsafe(32)
    expired_sid = secrets.token_urlsafe(32)

    await storage.create_browser_session(
        session_id=fresh_sid, user_id="alice", ttl_seconds=3600
    )
    # ttl_seconds=-2 → expires_at strictly in the past (cleanup uses < now,
    # so it must be actually less, not equal).
    await storage.create_browser_session(
        session_id=expired_sid, user_id="bob", ttl_seconds=-2
    )

    deleted = await storage.cleanup_expired_browser_sessions()
    assert deleted == 1

    # Fresh row survives, expired row is gone
    assert await storage.get_browser_session_user(fresh_sid) == "alice"
    assert await storage.get_browser_session_user(expired_sid) is None
    # Calling again should be a no-op
    assert await storage.cleanup_expired_browser_sessions() == 0
