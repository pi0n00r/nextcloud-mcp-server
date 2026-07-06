"""Unit tests for GatewaySecretMiddleware."""

import pytest

from nextcloud_mcp_server.app import GatewaySecretMiddleware


class MockApp:
    """Mock ASGI app for testing middleware."""

    def __init__(self):
        self.called = False
        self.received_scope = None

    async def __call__(self, scope, receive, send):
        self.called = True
        self.received_scope = scope


async def _receive():
    return {"type": "http.request", "body": b"", "more_body": False}


@pytest.mark.unit
async def test_gateway_secret_middleware_rejects_missing_secret():
    """Reject HTTP requests that do not present the configured secret."""
    mock_app = MockApp()
    middleware = GatewaySecretMiddleware(mock_app, "expected-secret")
    messages = []

    async def send(message):
        messages.append(message)

    scope = {"type": "http", "path": "/mcp", "headers": []}

    await middleware(scope, _receive, send)

    assert not mock_app.called
    assert messages[0]["type"] == "http.response.start"
    assert messages[0]["status"] == 401
    assert (b"www-authenticate", b'Bearer realm="mcp-gateway"') in messages[0][
        "headers"
    ]


@pytest.mark.unit
async def test_gateway_secret_middleware_accepts_secret_header():
    """Allow HTTP requests that present X-MCP-Gateway-Secret."""
    mock_app = MockApp()
    middleware = GatewaySecretMiddleware(mock_app, "expected-secret")
    scope = {
        "type": "http",
        "path": "/mcp",
        "headers": [(b"x-mcp-gateway-secret", b"expected-secret")],
    }

    await middleware(scope, _receive, None)  # type: ignore[arg-type]

    assert mock_app.called
    assert mock_app.received_scope is scope


@pytest.mark.unit
async def test_gateway_secret_middleware_accepts_bearer_secret():
    """Allow HTTP requests that present Authorization: Bearer."""
    mock_app = MockApp()
    middleware = GatewaySecretMiddleware(mock_app, "expected-secret")
    scope = {
        "type": "http",
        "path": "/mcp",
        "headers": [(b"authorization", b"Bearer expected-secret")],
    }

    await middleware(scope, _receive, None)  # type: ignore[arg-type]

    assert mock_app.called


@pytest.mark.unit
async def test_gateway_secret_middleware_exempts_open_prefixes():
    """Allow health and OAuth discovery routes without the gateway secret."""
    mock_app = MockApp()
    middleware = GatewaySecretMiddleware(mock_app, "expected-secret")

    for path in ("/health/live", "/.well-known/oauth-protected-resource"):
        mock_app.called = False
        scope = {"type": "http", "path": path, "headers": []}

        await middleware(scope, _receive, None)  # type: ignore[arg-type]

        assert mock_app.called


@pytest.mark.unit
async def test_gateway_secret_middleware_ignores_non_http_scopes():
    """Pass through non-HTTP scopes unchanged."""
    mock_app = MockApp()
    middleware = GatewaySecretMiddleware(mock_app, "expected-secret")
    scope = {"type": "websocket", "path": "/mcp", "headers": []}

    await middleware(scope, _receive, None)  # type: ignore[arg-type]

    assert mock_app.called
    assert mock_app.received_scope is scope
