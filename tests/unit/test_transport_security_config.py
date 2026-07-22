"""Tests for configurable MCP transport security (DNS rebinding protection).

The server always passes an explicit ``TransportSecuritySettings`` to FastMCP,
so FastMCP's loopback auto-enablement never applies. These tests pin the
resulting behavior: off by default (unchanged for existing deployments), and
correctly populated when an operator opts in.
"""

import logging

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
