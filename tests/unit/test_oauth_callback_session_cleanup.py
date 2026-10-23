"""Pin one-time-use semantics on the Flow-2 callback's oauth_session row.

The PR #758 follow-up review flagged that
``oauth_callback_nextcloud`` reads ``code_verifier`` from the
``oauth_sessions`` table but never deletes the row, leaving the verifier
valid for the rest of the 10-minute TTL. This test exercises the real
storage layer to confirm the row is gone after the callback runs.

We mock everything *after* the deletion (discovery + token exchange +
ID token verification) so the test focuses on the cleanup contract,
not the OAuth wire protocol.

Also pins the AS-proxy callback's ID-token verification rejection path
introduced in PR #758 finding 1 (auto-review): a forged or unsigned
id_token must surface as a 400 ``invalid_token`` JSONResponse and must
not register a proxy code.
"""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from nextcloud_mcp_server.auth.browser_oauth_routes import oauth_login_callback
from nextcloud_mcp_server.auth.oauth_routes import (
    ASProxySession,
    _as_proxy_sessions,
    _oauth_callback_as_proxy,
    _proxy_codes,
    oauth_callback_nextcloud,
)
from nextcloud_mcp_server.auth.storage import RefreshTokenStorage
from nextcloud_mcp_server.auth.token_utils import IdTokenVerificationError

pytestmark = pytest.mark.unit


@pytest.fixture
async def storage():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_callback_cleanup.db"
        s = RefreshTokenStorage(
            db_path=str(db_path), encryption_key=Fernet.generate_key().decode()
        )
        await s.initialize()
        yield s


def _build_request(*, code: str, state: str, storage: RefreshTokenStorage):
    request = MagicMock()
    request.query_params = {"code": code, "state": state}
    request.app.state.oauth_context = {
        "storage": storage,
        "config": {
            "discovery_url": "https://idp.example.com/.well-known/openid-configuration",
            "mcp_server_url": "https://mcp.example.com",
            "client_id": "mcp-server",
            "client_secret": "mcp-secret",
        },
    }
    return request


async def test_callback_deletes_oauth_session_after_reading_verifier(storage):
    """After a successful callback exchange the row is gone.

    Pins the PR #758 follow-up review fix: previously the row stayed
    until the 10-minute TTL elapsed, leaving the stored ``code_verifier``
    valid for replay if ``state`` leaked.
    """
    state = "state-abc-123"
    await storage.store_oauth_session(
        session_id=state,
        client_redirect_uri="http://localhost:9999/callback",
        state=state,
        mcp_authorization_code="verifier-pkce-secret",
        flow_type="flow2",
    )
    # Sanity check: row exists before the callback runs.
    assert await storage.get_oauth_session(state) is not None

    request = _build_request(code="idp-auth-code", state=state, storage=storage)

    # Stub everything after the deletion: discovery, token exchange, ID
    # token verification, and the user_oidc UserInfo round-trip. The
    # exact responses don't matter — we only care that the deletion has
    # happened by the time these are invoked.
    fake_discovery = {
        "token_endpoint": "https://idp.example.com/token",
        "userinfo_endpoint": "https://idp.example.com/userinfo",
        "issuer": "https://idp.example.com",
    }
    fake_userinfo = {"sub": "alice", "email": "alice@example.com"}
    fake_token_response = MagicMock()
    fake_token_response.json.return_value = {
        "access_token": "ac-tok",
        "refresh_token": "rf-tok",
        "id_token": "id-tok",
        "expires_in": 3600,
    }
    fake_token_response.raise_for_status = MagicMock()

    fake_http = MagicMock()
    fake_http.post = AsyncMock(return_value=fake_token_response)
    fake_http.__aenter__ = AsyncMock(return_value=fake_http)
    fake_http.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "nextcloud_mcp_server.auth.oauth_routes.get_oidc_discovery",
            new=AsyncMock(return_value=fake_discovery),
        ),
        patch(
            "nextcloud_mcp_server.auth.oauth_routes.nextcloud_httpx_client",
            return_value=fake_http,
        ),
        patch(
            "nextcloud_mcp_server.auth.oauth_routes.verify_id_token",
            new=AsyncMock(return_value=fake_userinfo),
        ),
    ):
        # The callback may go on to do extra work (storing tokens, redirecting,
        # rendering HTML); we don't care about the response body, only the
        # storage-level side effect.
        try:
            await oauth_callback_nextcloud(request)
        except Exception:
            # Any error past the deletion point is fine for this test.
            pass

    assert await storage.get_oauth_session(state) is None, (
        "oauth_callback_nextcloud must delete the oauth_sessions row "
        "after reading code_verifier (PR #758 follow-up review)"
    )


async def test_callback_unknown_state_returns_400(storage):
    """Unknown/expired state must fail closed with 400.

    Pins the PR #758 round-6 review fix: previously the callback fell
    through with empty ``code_verifier`` / ``expected_nonce=None``,
    silently bypassing the PKCE + nonce protections introduced in earlier
    rounds. The handler now returns 400 before any token exchange.
    """
    state = "state-missing"
    # No store_oauth_session call — the row never existed.

    request = _build_request(code="idp-auth-code", state=state, storage=storage)

    response = await oauth_callback_nextcloud(request)

    assert response.status_code == 400
    assert await storage.get_oauth_session(state) is None


async def test_browser_callback_unknown_state_returns_400(storage):
    """Symmetric unknown-state contract for the browser-flow callback.

    Mirrors ``test_callback_unknown_state_returns_400`` for
    ``oauth_login_callback`` — both callbacks must fail closed when the
    oauth_session row is missing/expired (PR #758 round-6 review).
    """
    state = "state-missing-browser"

    request = MagicMock()
    request.query_params = {"code": "idp-auth-code", "state": state}
    request.cookies = {}
    request.url_for = MagicMock(return_value="/oauth/login")
    request.app.state.oauth_context = {
        "storage": storage,
        "oauth_client": None,  # Nextcloud-integrated mode
        "config": {
            "mcp_server_url": "https://mcp.example.com",
            "client_id": "mcp-server",
            "client_secret": "mcp-secret",
        },
    }

    response = await oauth_login_callback(request)

    assert response.status_code == 400
    assert await storage.get_oauth_session(state) is None


# ---------------------------------------------------------------------------
# AS proxy callback (PR #758 finding 1): ID-token verification rejection
# ---------------------------------------------------------------------------


def _build_as_proxy_request(*, code: str, state: str):
    request = MagicMock()
    request.query_params = {"code": code, "state": state}
    request.app.state.oauth_context = {
        "config": {
            "discovery_url": "https://idp.example.com/.well-known/openid-configuration",
            "mcp_server_url": "https://mcp.example.com",
            "client_id": "mcp-server",
            "client_secret": "mcp-secret",
        }
    }
    return request


async def test_as_proxy_rejects_invalid_id_token():
    """Forged/unsigned id_token in the IdP token response → 400 invalid_token.

    Pins PR #758 finding 1. Without verification a compromised IdP or
    tampered transport could plant arbitrary identity claims into the
    cached ProxyCodeEntry that downstream clients pick up.
    """
    server_state = "as-proxy-state-rejected"
    _as_proxy_sessions[server_state] = ASProxySession(
        client_id="mcp-client",
        client_redirect_uri="http://127.0.0.1:9999/callback",
        client_state="client-state-xyz",
        code_challenge="challenge",
        code_challenge_method="S256",
        requested_scopes="openid",
        nonce="nonce-rejected",
    )
    _proxy_codes.clear()

    request = _build_as_proxy_request(code="auth-code", state=server_state)

    fake_discovery = {
        "token_endpoint": "https://idp.example.com/token",
        "issuer": "https://idp.example.com",
    }
    fake_token_response = MagicMock(status_code=200)
    fake_token_response.json.return_value = {
        "access_token": "ac-tok",
        "refresh_token": "rf-tok",
        "id_token": "forged.id.token",
        "token_type": "Bearer",
    }

    fake_http = MagicMock()
    fake_http.post = AsyncMock(return_value=fake_token_response)
    fake_http.__aenter__ = AsyncMock(return_value=fake_http)
    fake_http.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "nextcloud_mcp_server.auth.oauth_routes.get_oidc_discovery",
            new=AsyncMock(return_value=fake_discovery),
        ),
        patch(
            "nextcloud_mcp_server.auth.oauth_routes.nextcloud_httpx_client",
            return_value=fake_http,
        ),
        patch(
            "nextcloud_mcp_server.auth.oauth_routes.verify_id_token",
            new=AsyncMock(side_effect=IdTokenVerificationError("bad signature")),
        ),
    ):
        response = await _oauth_callback_as_proxy(request, server_state)

    assert response.status_code == 400
    body = bytes(response.body).decode()
    assert "invalid_token" in body
    # Critical: the proxy code store must not have grown — a rejected
    # callback must not be turned into a redeemable proxy code.
    assert _proxy_codes == {}
    # And the session has been popped (one-time use).
    assert server_state not in _as_proxy_sessions


async def test_as_proxy_passes_session_nonce_to_verify_id_token():
    """The session-bound nonce must be forwarded as ``expected_nonce``.

    Pins PR #758 round-2 finding 2: ``oauth_authorize`` generates a nonce
    and stores it on the ``ASProxySession``; the callback must pass it to
    ``verify_id_token`` so an ID token harvested from a parallel auth
    request can't be replayed inside the AS-proxy flow.
    """
    server_state = "as-proxy-state-with-nonce"
    server_nonce = "nonce-bound-to-this-request"
    _as_proxy_sessions[server_state] = ASProxySession(
        client_id="mcp-client",
        client_redirect_uri="http://127.0.0.1:9999/callback",
        client_state="client-state",
        code_challenge="challenge",
        code_challenge_method="S256",
        requested_scopes="openid",
        nonce=server_nonce,
    )
    _proxy_codes.clear()

    request = _build_as_proxy_request(code="auth-code", state=server_state)

    fake_discovery = {
        "token_endpoint": "https://idp.example.com/token",
        "issuer": "https://idp.example.com",
    }
    fake_token_response = MagicMock(status_code=200)
    fake_token_response.json.return_value = {
        "access_token": "ac",
        "id_token": "id-tok",
        "token_type": "Bearer",
    }
    fake_http = MagicMock()
    fake_http.post = AsyncMock(return_value=fake_token_response)
    fake_http.__aenter__ = AsyncMock(return_value=fake_http)
    fake_http.__aexit__ = AsyncMock(return_value=None)

    verify_mock = AsyncMock(return_value={"sub": "alice"})

    with (
        patch(
            "nextcloud_mcp_server.auth.oauth_routes.get_oidc_discovery",
            new=AsyncMock(return_value=fake_discovery),
        ),
        patch(
            "nextcloud_mcp_server.auth.oauth_routes.nextcloud_httpx_client",
            return_value=fake_http,
        ),
        patch(
            "nextcloud_mcp_server.auth.oauth_routes.verify_id_token",
            new=verify_mock,
        ),
    ):
        await _oauth_callback_as_proxy(request, server_state)

    verify_mock.assert_awaited_once()
    kwargs = verify_mock.await_args.kwargs
    assert kwargs.get("expected_nonce") == server_nonce, (
        "AS-proxy callback must forward session.nonce to verify_id_token"
    )
