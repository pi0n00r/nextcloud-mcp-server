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
"""Focused contract tests for Bridgette Personal live API discovery."""

from __future__ import annotations

from typing import Any

import anyio
import httpx2
import pytest
from mcp.server.mcpserver import MCPServer

from nextcloud_mcp_server.config import Settings
from nextcloud_mcp_server.personal_dynamic_api import (
    PersonalDynamicCatalogue,
    configure_personal_dynamic_tools,
)


def _document() -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "paths": {
            "/ocs/v2.php/apps/demo/items/{item_id}": {
                "get": {
                    "operationId": "item",
                    "parameters": [
                        {
                            "in": "path",
                            "name": "item_id",
                            "required": True,
                            "schema": {"type": "integer", "minimum": 1},
                        },
                        {
                            "in": "query",
                            "name": "tag",
                            "schema": {"type": "array", "items": {"type": "string"}},
                        },
                    ],
                }
            },
            "/ocs/v2.php/apps/demo/items": {
                "post": {
                    "operationId": "item",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "title": {"type": "string", "minLength": 1}
                                    },
                                    "required": ["title"],
                                    "additionalProperties": False,
                                }
                            }
                        },
                    },
                }
            },
            "/ocs/v2.php/apps/demo/upload": {
                "post": {
                    "operationId": "upload",
                    "requestBody": {
                        "content": {
                            "multipart/form-data": {"schema": {"type": "object"}}
                        }
                    },
                }
            },
            "/ocs/v2.php/apps/demo/protected": {
                "get": {
                    "operationId": "protected",
                    "parameters": [
                        {
                            "in": "header",
                            "name": "Authorization",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                }
            },
        },
    }


def _catalogue(handler) -> PersonalDynamicCatalogue:
    settings = Settings(
        nextcloud_host="https://cloud.example.com",
        nextcloud_username="gary",
        nextcloud_password="secret",
        deployment_mode="single_user_basic",
        bridgette_edition="personal",
    )

    def factory():
        return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))

    return PersonalDynamicCatalogue(settings, client_factory=factory)


def test_dynamic_tools_expose_titles_and_safety_annotations():
    mcp = MCPServer("personal-dynamic-test")
    configure_personal_dynamic_tools(mcp, catalogue=_catalogue(lambda _: None))
    tools = {tool.name: tool for tool in mcp._tool_manager.list_tools()}

    status = tools["nc_dynamic_discovery_status"]
    search = tools["nc_dynamic_search_operations"]
    call = tools["nc_dynamic_call"]

    assert status.title and status.annotations is not None
    assert search.title and search.annotations is not None
    assert call.title and call.annotations is not None
    assert status.annotations.read_only_hint is True
    assert search.annotations.read_only_hint is True
    assert call.annotations.read_only_hint is False
    assert call.annotations.idempotent_hint is False


@pytest.mark.anyio
async def test_complete_discovery_skips_unsupported_and_disambiguates_names():
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/apps"):
            return httpx2.Response(200, json={"ocs": {"data": [{"id": "demo"}]}})
        return httpx2.Response(200, json={"ocs": {"data": _document()}})

    catalogue = _catalogue(handler)
    state = await catalogue.refresh()

    assert state.complete is True
    assert state.generation == 1
    assert len(state.operations) == 2
    assert len(set(state.operations)) == 2
    assert state.apps[0]["unsupported_operation_count"] == 2


@pytest.mark.anyio
async def test_malformed_operation_is_skipped_without_disabling_app():
    document = _document()
    document["paths"]["/ocs/v2.php/apps/demo/malformed"] = {
        "get": {"operationId": "malformed", "parameters": None}
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/apps"):
            return httpx2.Response(200, json={"ocs": {"data": [{"id": "demo"}]}})
        return httpx2.Response(200, json={"ocs": {"data": document}})

    state = await _catalogue(handler).refresh()

    assert state.complete is True
    assert len(state.operations) == 2
    assert state.apps[0]["unsupported_operation_count"] == 3


@pytest.mark.anyio
async def test_cancelled_first_refresh_is_retried(monkeypatch):
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/apps"):
            return httpx2.Response(200, json={"ocs": {"data": [{"id": "demo"}]}})
        return httpx2.Response(200, json={"ocs": {"data": _document()}})

    catalogue = _catalogue(handler)
    original = catalogue._bounded_json
    first = True

    async def cancel_first(client, url):
        nonlocal first
        if first:
            first = False
            await anyio.sleep_forever()
        return await original(client, url)

    monkeypatch.setattr(catalogue, "_bounded_json", cancel_first)
    with anyio.move_on_after(0.01) as scope:
        await catalogue.ensure()
    assert scope.cancel_called is True

    state = await catalogue.ensure()
    assert state.complete is True
    assert state.generation == 1


@pytest.mark.anyio
async def test_schema_validation_precedes_request_and_preserves_query_arrays():
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/apps"):
            return httpx2.Response(200, json={"ocs": {"data": [{"id": "demo"}]}})
        if request.url.path.endswith("/apps/demo"):
            return httpx2.Response(200, json={"ocs": {"data": _document()}})
        seen.append(request)
        return httpx2.Response(200, json={"ok": True})

    catalogue = _catalogue(handler)
    await catalogue.refresh()
    get_name = next(
        name for name, op in catalogue.state.operations.items() if op.method == "GET"
    )

    with pytest.raises(ValueError, match="item_id"):
        await catalogue.call(get_name, {"path": {"item_id": "not-an-integer"}})
    assert seen == []

    result = await catalogue.call(
        get_name,
        {"path": {"item_id": 7}, "query": {"tag": ["one", "two"]}},
    )
    assert result["ok"] is True
    assert seen[0].url.path.endswith("/items/7")
    assert seen[0].url.params.get_list("tag") == ["one", "two"]


@pytest.mark.anyio
async def test_dot_segments_are_rejected_before_path_resolution():
    seen: list[httpx2.Request] = []
    document = _document()
    document["paths"]["/ocs/v2.php/apps/demo/items/{item_id}"]["get"]["parameters"][0][
        "schema"
    ] = {"type": "string"}

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/apps"):
            return httpx2.Response(200, json={"ocs": {"data": [{"id": "demo"}]}})
        if request.url.path.endswith("/apps/demo"):
            return httpx2.Response(200, json={"ocs": {"data": document}})
        seen.append(request)
        return httpx2.Response(200, json={"ok": True})

    catalogue = _catalogue(handler)
    await catalogue.refresh()
    get_name = next(
        name for name, op in catalogue.state.operations.items() if op.method == "GET"
    )

    for value in (".", ".."):
        with pytest.raises(ValueError, match="safely resolved"):
            await catalogue.call(get_name, {"path": {"item_id": value}})
    assert seen == []


@pytest.mark.anyio
async def test_failed_refresh_retains_last_complete_but_requires_stale_consent():
    failing = False

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/apps"):
            if failing:
                return httpx2.Response(503)
            return httpx2.Response(200, json={"ocs": {"data": [{"id": "demo"}]}})
        if request.url.path.endswith("/apps/demo"):
            return httpx2.Response(200, json={"ocs": {"data": _document()}})
        return httpx2.Response(200, json={"ok": True})

    catalogue = _catalogue(handler)
    await catalogue.refresh()
    get_name = next(
        name for name, op in catalogue.state.operations.items() if op.method == "GET"
    )
    failing = True
    state = await catalogue.refresh()
    assert state.stale is True
    assert state.generation == 1
    with pytest.raises(RuntimeError, match="explicitly allow stale"):
        await catalogue.call(get_name, {"path": {"item_id": 1}})
    result = await catalogue.call(
        get_name,
        {"path": {"item_id": 1}},
        allow_stale=True,
    )
    assert result["used_stale_catalogue"] is True


@pytest.mark.anyio
async def test_response_byte_ceiling_fails_closed(monkeypatch):
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/apps"):
            return httpx2.Response(200, json={"ocs": {"data": [{"id": "demo"}]}})
        if request.url.path.endswith("/apps/demo"):
            return httpx2.Response(200, json={"ocs": {"data": _document()}})
        return httpx2.Response(200, content=b"0123456789")

    monkeypatch.setattr(
        "nextcloud_mcp_server.personal_dynamic_api._MAX_RESPONSE_BYTES", 5
    )
    catalogue = _catalogue(handler)
    await catalogue.refresh()
    get_name = next(
        name for name, op in catalogue.state.operations.items() if op.method == "GET"
    )
    with pytest.raises(ValueError, match="exceeds byte limit"):
        await catalogue.call(get_name, {"path": {"item_id": 1}})
