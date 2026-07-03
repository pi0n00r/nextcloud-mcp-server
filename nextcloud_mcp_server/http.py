"""Centralized HTTP client factory for Nextcloud connections.

All outbound connections to Nextcloud (API calls, OIDC endpoints) should use
these factories to ensure consistent SSL/TLS configuration from environment
variables (NEXTCLOUD_VERIFY_SSL, NEXTCLOUD_CA_BUNDLE).
"""

from typing import Any

import httpx

from .config import get_nextcloud_ssl_verify


NEXTCLOUD_KEEPALIVE_EXPIRY_SECONDS = 5.0


def nextcloud_httpx_client(**kwargs: Any) -> httpx.AsyncClient:
    """Create an httpx.AsyncClient with Nextcloud SSL settings applied.

    Reads NEXTCLOUD_VERIFY_SSL and NEXTCLOUD_CA_BUNDLE from the environment
    via ``get_nextcloud_ssl_verify()``. Caller-supplied ``verify`` kwarg
    takes precedence if explicitly provided.

    Args:
        **kwargs: Forwarded to ``httpx.AsyncClient()``.

    Returns:
        Configured ``httpx.AsyncClient``.
    """
    kwargs.setdefault("verify", get_nextcloud_ssl_verify())
    return httpx.AsyncClient(**kwargs)


def nextcloud_httpx_transport(**kwargs: Any) -> httpx.AsyncHTTPTransport:
    """Create an httpx.AsyncHTTPTransport with Nextcloud SSL settings applied.

    Used by ``NextcloudClient`` which wraps the transport in
    ``AsyncDisableCookieTransport``.

    The transport keeps httpx pooling enabled, but pins a short keep-alive idle
    expiry so stale Nextcloud/WebDAV connections age out promptly. A
    caller-supplied ``limits`` kwarg takes precedence.

    ``get_nextcloud_ssl_verify()`` is read eagerly here. That is correct because
    a transport is built once per client lifetime; a future refactor that builds
    transports per-request would turn this into a per-request ``get_settings()``
    call and should cache it.

    Args:
        **kwargs: Forwarded to ``httpx.AsyncHTTPTransport()``.

    Returns:
        Configured ``httpx.AsyncHTTPTransport``.
    """
    kwargs.setdefault("verify", get_nextcloud_ssl_verify())
    kwargs.setdefault(
        "limits", httpx.Limits(keepalive_expiry=NEXTCLOUD_KEEPALIVE_EXPIRY_SECONDS)
    )
    return httpx.AsyncHTTPTransport(**kwargs)
