"""Centralized HTTP client factory for Nextcloud connections.

All outbound connections to Nextcloud (API calls, OIDC endpoints) should use
these factories to ensure consistent SSL/TLS configuration from environment
variables (NEXTCLOUD_VERIFY_SSL, NEXTCLOUD_CA_BUNDLE).
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

import httpx

from .config import get_nextcloud_ssl_verify

NEXTCLOUD_KEEPALIVE_EXPIRY_SECONDS = 5.0

#: Sent on every request to Nextcloud. ``OCS-APIRequest: true`` is how a
#: non-browser client identifies itself: OCS routes refuse requests without it
#: (``meta.statuscode: 997``), it exempts app routes from the CSRF check, and on
#: ``#[CORS]`` app routes it stops ``CORSMiddleware`` from logging the session
#: out and re-authenticating -- the path that 404s Tables v1 on Nextcloud 33
#: (GH #1664). This is the only place the header is spelled; per-request
#: headers merge over it. Read-only for the same reason as
#: ``ocs.OCS_REQUEST_HEADERS``: every client shares this one object.
NEXTCLOUD_DEFAULT_HEADERS: Mapping[str, str] = MappingProxyType(
    {"OCS-APIRequest": "true"}
)


def nextcloud_httpx_client(**kwargs: Any) -> httpx.AsyncClient:
    """Create an httpx.AsyncClient with Nextcloud SSL settings and headers applied.

    Reads NEXTCLOUD_VERIFY_SSL and NEXTCLOUD_CA_BUNDLE from the environment
    via ``get_nextcloud_ssl_verify()``. Caller-supplied ``verify`` kwarg
    takes precedence if explicitly provided. ``NEXTCLOUD_DEFAULT_HEADERS``
    are client defaults; caller-supplied ``headers`` are merged over them.

    Nextcloud-specific: build clients for other services (Qdrant, the
    embedding gateway, document processors, ...) with ``httpx.AsyncClient``
    directly. The configured OIDC provider is the one sanctioned exception,
    because it shares Nextcloud's TLS settings; it ignores the extra header.

    Args:
        **kwargs: Forwarded to ``httpx.AsyncClient()``.

    Returns:
        Configured ``httpx.AsyncClient``.
    """
    kwargs.setdefault("verify", get_nextcloud_ssl_verify())
    # httpx.Headers: case-insensitive override, and accepts every header shape
    # httpx does (mapping, Headers, list of tuples).
    headers = httpx.Headers(NEXTCLOUD_DEFAULT_HEADERS)
    headers.update(kwargs.get("headers") or {})
    kwargs["headers"] = headers
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
