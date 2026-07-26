# AI-NOTICE:Schema-Version=0.1
# AI-NOTICE:License=AGPL-3.0-or-later
# AI-NOTICE:Author=Gary Bajaj
# AI-NOTICE:Exploitation-Deterrence=true
# AI-NOTICE:Operator-Override-Required=true
# AI-NOTICE:Override-Reason-Required=false
# AI-NOTICE:Severity=high
# AI-NOTICE:Escalation=warn
# AI-NOTICE:Scope=file
# AI-NOTICE:Contact=https://AImends.bajaj.com/

"""Tests for configurable MCP transport security and CORS."""

from __future__ import annotations

import logging

import httpx
import pytest
from mcp.server.transport_security import TransportSecurityMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from nextcloud_mcp_server.app import (
    _split_csv,
    build_cors_origins,
    build_transport_security,
)
from nextcloud_mcp_server.config import Settings

pytestmark = pytest.mark.unit


class TestSplitCsv:
    """Comma-separated transport and CORS setting parsing."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (None, []),
            ("", []),
            ("   ", []),
            ("*", ["*"]),
            ("localhost:*", ["localhost:*"]),
            (" a:* , b:* ", ["a:*", "b:*"]),
            ("a:*,,b:*,", ["a:*", "b:*"]),
        ],
    )
    def test_split_csv(self, raw, expected):
        assert _split_csv(raw) == expected


class TestBuildTransportSecurity:
    """Translation of Settings into TransportSecuritySettings."""

    def test_disabled_by_default(self):
        result = build_transport_security(Settings())

        assert result.enable_dns_rebinding_protection is False

    def test_allowlists_do_not_enable_protection(self, caplog):
        settings = Settings(
            mcp_allowed_hosts="nextcloud-mcp:*",
            mcp_allowed_origins="https://example.com",
        )

        with caplog.at_level(logging.WARNING, logger="nextcloud_mcp_server.app"):
            result = build_transport_security(settings)

        assert result.enable_dns_rebinding_protection is False
        assert "have no effect" in caplog.text

    def test_enabled_populates_canonical_allowlists(self):
        settings = Settings(
            mcp_dns_rebinding_protection=True,
            mcp_allowed_hosts="nextcloud-mcp:*,127.0.0.1:*",
            mcp_allowed_origins="https://example.com",
        )

        result = build_transport_security(settings)

        assert result.enable_dns_rebinding_protection is True
        assert result.allowed_hosts == ["nextcloud-mcp:*", "127.0.0.1:*"]
        assert result.allowed_origins == ["https://example.com"]

    def test_enabled_without_origins_is_allowed(self):
        settings = Settings(
            mcp_dns_rebinding_protection=True,
            mcp_allowed_hosts="localhost:*",
        )

        result = build_transport_security(settings)

        assert result.enable_dns_rebinding_protection is True
        assert result.allowed_origins == []

    def test_enabled_with_empty_hosts_fails_at_startup(self):
        with pytest.raises(ValueError, match="MCP_ALLOWED_HOSTS"):
            build_transport_security(Settings(mcp_dns_rebinding_protection=True))

    def test_deprecated_fork_allowlists_remain_compatible(self, caplog):
        settings = Settings(
            mcp_dns_rebinding_protection=True,
            mcp_dns_rebinding_allowed_hosts="legacy.internal:*",
            mcp_dns_rebinding_allowed_origins="https://legacy.example",
        )

        with caplog.at_level(logging.WARNING, logger="nextcloud_mcp_server.app"):
            result = build_transport_security(settings)

        assert result.allowed_hosts == ["legacy.internal:*"]
        assert result.allowed_origins == ["https://legacy.example"]
        assert "deprecated" in caplog.text

    def test_canonical_allowlists_override_deprecated_aliases(self, caplog):
        settings = Settings(
            mcp_dns_rebinding_protection=True,
            mcp_allowed_hosts="canonical.internal:*",
            mcp_dns_rebinding_allowed_hosts="legacy.internal:*",
        )

        with caplog.at_level(logging.WARNING, logger="nextcloud_mcp_server.app"):
            result = build_transport_security(settings)

        assert result.allowed_hosts == ["canonical.internal:*"]
        assert "using MCP_ALLOWED_HOSTS" in caplog.text


class TestCorsOrigins:
    """CORS remains permissive by default and accepts explicit allowlists."""

    def test_default_remains_wildcard(self, caplog):
        with caplog.at_level(logging.WARNING, logger="nextcloud_mcp_server.app"):
            origins = build_cors_origins(Settings())

        assert origins == ["*"]
        assert "allows any origin with credentials" in caplog.text

    def test_explicit_origins_are_parsed(self, caplog):
        settings = Settings(
            cors_allow_origins="https://one.example, https://two.example"
        )

        with caplog.at_level(logging.WARNING, logger="nextcloud_mcp_server.app"):
            origins = build_cors_origins(settings)

        assert origins == ["https://one.example", "https://two.example"]
        assert "allows any origin with credentials" not in caplog.text

    def test_empty_value_preserves_legacy_wildcard(self):
        assert build_cors_origins(Settings(cors_allow_origins="")) == ["*"]


class TestSettingsDefaults:
    """Transport and CORS defaults preserve pre-merge behavior."""

    def test_transport_defaults(self):
        settings = Settings()

        assert settings.mcp_dns_rebinding_protection is False
        assert settings.mcp_allowed_hosts == ""
        assert settings.mcp_allowed_origins == ""
        assert settings.mcp_dns_rebinding_allowed_hosts == ""
        assert settings.mcp_dns_rebinding_allowed_origins == ""

    def test_cors_defaults_to_wildcard(self):
        assert Settings().cors_allow_origins == "*"


def _guarded_app(settings: Settings):
    """Build a tiny ASGI app guarded by the MCP transport middleware."""
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
            mcp_allowed_hosts="mcp.internal:*",
        )

        response = await _request(settings, host="attacker.example:9000")

        assert response.status_code == 421
        assert response.text == "Invalid Host header"

    async def test_accepts_allowed_host_without_origin(self):
        settings = Settings(
            mcp_dns_rebinding_protection=True,
            mcp_allowed_hosts="mcp.internal:*",
        )

        response = await _request(settings, host="mcp.internal:9000")

        assert response.status_code == 200

    async def test_rejects_origin_outside_allowlist(self):
        settings = Settings(
            mcp_dns_rebinding_protection=True,
            mcp_allowed_hosts="mcp.internal:*",
            mcp_allowed_origins="https://operator.example",
        )

        response = await _request(
            settings,
            host="mcp.internal:9000",
            origin="https://attacker.example",
        )

        assert response.status_code == 403
        assert response.text == "Invalid Origin header"

    async def test_accepts_allowed_origin(self):
        settings = Settings(
            mcp_dns_rebinding_protection=True,
            mcp_allowed_hosts="mcp.internal:*",
            mcp_allowed_origins="https://operator.example",
        )

        response = await _request(
            settings,
            host="mcp.internal:9000",
            origin="https://operator.example",
        )

        assert response.status_code == 200

    async def test_enabled_with_empty_hosts_refuses_to_build(self):
        with pytest.raises(ValueError, match="MCP_ALLOWED_HOSTS"):
            await _request(
                Settings(mcp_dns_rebinding_protection=True),
                host="mcp.internal:9000",
            )

    async def test_default_off_preserves_existing_host_behavior(self):
        response = await _request(Settings(), host="arbitrary.internal:9000")

        assert response.status_code == 200
