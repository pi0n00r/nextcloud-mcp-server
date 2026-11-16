"""Edition boundary and registration tests."""

from __future__ import annotations

import pytest

from nextcloud_mcp_server.config import Settings, _reload_config
from nextcloud_mcp_server.config_validators import AuthMode, validate_configuration
from nextcloud_mcp_server.stdio import get_stdio_mcp


def test_personal_rejects_multi_user_auth_mode():
    settings = Settings(
        nextcloud_host="https://cloud.example.com",
        nextcloud_username="gary",
        nextcloud_password="secret",
        deployment_mode="multi_user_basic",
        bridgette_edition="personal",
    )
    mode, errors = validate_configuration(settings)
    assert mode == AuthMode.MULTI_USER_BASIC
    assert any("BRIDGETTE_EDITION=personal requires" in error for error in errors)


def test_invalid_edition_and_timeout_fail_at_configuration():
    with pytest.raises(ValueError, match="Invalid BRIDGETTE_EDITION"):
        Settings(bridgette_edition="enterprise")
    with pytest.raises(ValueError, match="DYNAMIC_API_TIMEOUT_SECONDS"):
        Settings(dynamic_api_timeout_seconds=0)


@pytest.mark.parametrize(
    ("edition", "dynamic_expected"),
    [("multi_user", False), ("personal", True)],
)
def test_stdio_registers_dynamic_tools_only_for_personal(
    monkeypatch,
    edition: str,
    dynamic_expected: bool,
):
    monkeypatch.setenv("NEXTCLOUD_HOST", "https://cloud.example.com")
    monkeypatch.setenv("NEXTCLOUD_USERNAME", "gary")
    monkeypatch.setenv("NEXTCLOUD_PASSWORD", "secret")
    monkeypatch.setenv("MCP_DEPLOYMENT_MODE", "single_user_basic")
    monkeypatch.setenv("BRIDGETTE_EDITION", edition)
    _reload_config()
    try:
        tools = {
            tool.name
            for tool in get_stdio_mcp(enabled_apps=["notes"])._tool_manager.list_tools()
        }
    finally:
        _reload_config()
    assert ("nc_dynamic_call" in tools) is dynamic_expected
    assert "nc_notes_get_note" in tools
