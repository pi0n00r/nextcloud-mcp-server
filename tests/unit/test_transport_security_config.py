"""Tests for configurable MCP transport security (DNS rebinding protection).

The server always passes an explicit ``TransportSecuritySettings`` to FastMCP,
so FastMCP's loopback auto-enablement never applies. These tests pin the
resulting behavior: off by default (unchanged for existing deployments), and
correctly populated when an operator opts in.
"""

import logging

import httpx
from mcp.server.transport_security import TransportSecurityMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from nextcloud_mcp_server.app import _split_csv, build_transport_security
from nextcloud_mcp_server.config import Settings


class TestSplitCsv:
    """The comma-separated allowlist parser."""

    def test_empty_string_is_empty_list(self):
        assert _split_csv("") == []

    def test_none_is_empty_list(self):
        assert _split_csv(None) == []

    def test_single_entry(self):
        assert _split_csv("localhost:*") == ["localhost:*"]

    def test_strips_surrounding_whitespace(self):
        assert _split_csv(" a:* , b:* ") == ["a:*", "b:*"]

    def test_drops_blank_entries(self):
        """A trailing comma or double comma must not yield an empty host."""
        assert _split_csv("a:*,,b:*,") == ["a:*", "b:*"]

    def test_whitespace_only_is_empty_list(self):
        assert _split_csv("   ") == []


class TestBuildTransportSecurity:
    """Translation of Settings into TransportSecuritySettings."""

    def test_disabled_by_default(self):
        """Default config must reproduce the historical always-off behavior."""
        result = build_transport_security(Settings())
        assert result.enable_dns_rebinding_protection is False

    def test_allowlists_ignored_while_disabled(self):
        """Allowlists set without the enable flag must not silently switch it on."""
        settings = Settings(
            mcp_dns_rebinding_allowed_hosts="nextcloud-mcp:*",
            mcp_dns_rebinding_allowed_origins="https://example.com",
        )
        result = build_transport_security(settings)
        assert result.enable_dns_rebinding_protection is False

    def test_enabled_populates_allowlists(self):
        settings = Settings(
            mcp_dns_rebinding_protection=True,
            mcp_dns_rebinding_allowed_hosts="nextcloud-mcp:*,127.0.0.1:*",
            mcp_dns_rebinding_allowed_origins="https://example.com",
        )
        result = build_transport_security(settings)
        assert result.enable_dns_rebinding_protection is True
        assert result.allowed_hosts == ["nextcloud-mcp:*", "127.0.0.1:*"]
        assert result.allowed_origins == ["https://example.com"]

    def test_enabled_without_origins_is_allowed(self):
        """Origin is optional: the middleware permits an absent Origin header."""
        settings = Settings(
            mcp_dns_rebinding_protection=True,
            mcp_dns_rebinding_allowed_hosts="localhost:*",
        )
        result = build_transport_security(settings)
        assert result.enable_dns_rebinding_protection is True
        assert result.allowed_origins == []

    def test_enabled_with_empty_hosts_warns(self, caplog):
        """Host validation fails closed; an empty allowlist must not be silent."""
        settings = Settings(mcp_dns_rebinding_protection=True)
        with caplog.at_level(logging.WARNING, logger="nextcloud_mcp_server.app"):
            result = build_transport_security(settings)
        assert result.enable_dns_rebinding_protection is True
        assert result.allowed_hosts == []
        assert "every request will be rejected" in caplog.text

    def test_enabled_with_hosts_does_not_warn(self):
        settings = Settings(
            mcp_dns_rebinding_protection=True,
            mcp_dns_rebinding_allowed_hosts="localhost:*",
        )
        result = build_transport_security(settings)
        assert result.allowed_hosts == ["localhost:*"]


class TestSettingsDefaults:
    """The new fields default to the pre-change behavior."""

    def test_protection_defaults_off(self):
        assert Settings().mcp_dns_rebinding_protection is False

    def test_allowlists_default_empty(self):
        settings = Settings()
        assert settings.mcp_dns_rebinding_allowed_hosts == ""
        assert settings.mcp_dns_rebinding_allowed_origins == ""


def _guarded_app(settings: Settings):
    """Build a tiny ASGI app guarded by the same MCP transport middleware."""
    security = TransportSecurityMiddleware(build_transport_security(settings))

    async def app(scope, receive, send):
        request = Request(scope, receive)
        denial = await security.validate_request(
            request,
            is_post=request.method == "POST",
        )
        response = denial or PlainTextResponse("accepted")
        await response(scope, receive, send)

    return app


async def _request(settings: Settings, **headers: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=_guarded_app(settings))
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://transport.test",
    ) as client:
        return await client.get("/mcp", headers=headers)


class TestTransportSecurityRequests:
    """Exercise actual request decisions made by the MCP SDK middleware."""

    async def test_rejects_host_outside_allowlist(self):
        settings = Settings(
            mcp_dns_rebinding_protection=True,
            mcp_dns_rebinding_allowed_hosts="bridgette.internal:*",
        )

        response = await _request(settings, host="attacker.example:9000")

        assert response.status_code == 421
        assert response.text == "Invalid Host header"

    async def test_accepts_allowed_host_without_origin(self):
        settings = Settings(
            mcp_dns_rebinding_protection=True,
            mcp_dns_rebinding_allowed_hosts="bridgette.internal:*",
        )

        response = await _request(settings, host="bridgette.internal:9000")

        assert response.status_code == 200

    async def test_rejects_origin_outside_allowlist(self):
        settings = Settings(
            mcp_dns_rebinding_protection=True,
            mcp_dns_rebinding_allowed_hosts="bridgette.internal:*",
            mcp_dns_rebinding_allowed_origins="https://operator.example",
        )

        response = await _request(
            settings,
            host="bridgette.internal:9000",
            origin="https://attacker.example",
        )

        assert response.status_code == 403
        assert response.text == "Invalid Origin header"

    async def test_accepts_allowed_origin(self):
        settings = Settings(
            mcp_dns_rebinding_protection=True,
            mcp_dns_rebinding_allowed_hosts="bridgette.internal:*",
            mcp_dns_rebinding_allowed_origins="https://operator.example",
        )

        response = await _request(
            settings,
            host="bridgette.internal:9000",
            origin="https://operator.example",
        )

        assert response.status_code == 200

    async def test_enabled_with_empty_hosts_fails_closed(self):
        response = await _request(
            Settings(mcp_dns_rebinding_protection=True),
            host="bridgette.internal:9000",
        )

        assert response.status_code == 421

    async def test_default_off_preserves_existing_host_behavior(self):
        response = await _request(Settings(), host="arbitrary.internal:9000")

        assert response.status_code == 200
