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
"""Bounded live Nextcloud API discovery for Bridgette Personal."""

from __future__ import annotations

import base64
import copy
import hashlib
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import anyio
import httpx2
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from nextcloud_mcp_server.config import Settings, get_settings
from nextcloud_mcp_server.http import nextcloud_httpx_client

logger = logging.getLogger(__name__)

_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete"})
_BLOCKED_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "host",
        "ocs-apirequest",
        "proxy-authorization",
        "x-nextcloud-apptoken",
        "x-nextcloud-username",
    }
)
_SAFE_RESPONSE_HEADERS = frozenset(
    {"content-type", "etag", "last-modified", "location", "oc-fileid"}
)
_MAX_APPS = 100
_MAX_OPERATIONS = 2_000
_MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_DISCOVERY_DEADLINE_SECONDS = 90.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error_summary(exc: Exception) -> str:
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return f"{type(exc).__name__}:http_{status}" if status else type(exc).__name__


def _normalise_name(
    app_id: str, operation_id: str | None, method: str, path: str
) -> str:
    raw = operation_id or f"{method}_{path}"
    value = re.sub(r"[^a-zA-Z0-9]+", "_", f"{app_id}_{raw}").strip("_").lower()
    if len(value) <= 96:
        return value
    digest = hashlib.sha256(value.encode()).hexdigest()[:10]
    return f"{value[:85]}_{digest}"


def _unwrap_ocs(payload: Any) -> Any:
    if not isinstance(payload, dict) or not isinstance(payload.get("ocs"), dict):
        return payload
    return payload["ocs"].get("data", payload)


def _resolve_local_refs(document: dict[str, Any], value: Any, depth: int = 0) -> Any:
    if depth > 20:
        raise ValueError("OpenAPI reference depth exceeds 20")
    if isinstance(value, list):
        return [_resolve_local_refs(document, item, depth + 1) for item in value]
    if not isinstance(value, dict):
        return value
    ref = value.get("$ref")
    if isinstance(ref, str):
        if not ref.startswith("#/"):
            raise ValueError("External OpenAPI references are unsupported")
        target: Any = document
        for segment in ref[2:].split("/"):
            target = target[segment.replace("~1", "/").replace("~0", "~")]
        merged = copy.deepcopy(target)
        merged.update({key: item for key, item in value.items() if key != "$ref"})
        return _resolve_local_refs(document, merged, depth + 1)
    return {
        key: _resolve_local_refs(document, item, depth + 1)
        for key, item in value.items()
    }


def _simple_schema(schema: dict[str, Any], *, allow_array: bool) -> bool:
    if schema.get("type") in {"string", "integer", "number", "boolean"}:
        return True
    return bool(
        allow_array
        and schema.get("type") == "array"
        and _simple_schema(schema.get("items") or {}, allow_array=False)
    )


def _section_schema(parameters: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": "object",
        "properties": {
            item["name"]: copy.deepcopy(item["schema"]) for item in parameters
        },
        "additionalProperties": False,
    }
    required = [item["name"] for item in parameters if item["required"]]
    if required:
        result["required"] = required
    return result


@dataclass(frozen=True, slots=True)
class DynamicOperation:
    name: str
    app_id: str
    app_name: str
    method: str
    path: str
    summary: str
    description: str
    input_schema: dict[str, Any]
    path_params: tuple[str, ...]
    query_params: tuple[str, ...]
    header_params: tuple[str, ...]
    body_content_type: str | None

    def summary_payload(self) -> dict[str, Any]:
        return {
            "operation": self.name,
            "app_id": self.app_id,
            "app_name": self.app_name,
            "method": self.method,
            "path": self.path,
            "summary": self.summary,
            "description": self.description,
            "input_schema": self.input_schema,
        }


@dataclass(frozen=True, slots=True)
class DynamicCatalogueState:
    operations: dict[str, DynamicOperation] = field(default_factory=dict)
    apps: tuple[dict[str, Any], ...] = ()
    complete: bool = False
    stale: bool = False
    generation: int = 0
    last_attempt: str | None = None
    last_successful_refresh: str | None = None
    errors: tuple[dict[str, str], ...] = ()


def _build_operation(
    app: dict[str, Any],
    document: dict[str, Any],
    path: str,
    method: str,
    operation: dict[str, Any],
    inherited_parameters: list[dict[str, Any]],
) -> DynamicOperation:
    grouped: dict[str, list[dict[str, Any]]] = {"path": [], "query": [], "header": []}
    parameter_map: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in [*inherited_parameters, *operation.get("parameters", [])]:
        parameter = _resolve_local_refs(document, raw)
        location, name = parameter.get("in"), parameter.get("name")
        if location == "cookie":
            raise ValueError("cookie parameters are unsupported")
        if location in grouped and isinstance(name, str):
            parameter_map[(location, name)] = parameter

    for (location, name), parameter in parameter_map.items():
        if location == "header" and name.casefold() in _BLOCKED_HEADERS:
            if parameter.get("required"):
                raise ValueError("operation requires a protected header")
            continue
        schema = copy.deepcopy(parameter.get("schema") or {})
        if not _simple_schema(schema, allow_array=location == "query"):
            raise ValueError(f"unsupported {location} parameter schema")
        style, explode = parameter.get("style"), parameter.get("explode")
        if location == "path" and (
            style not in {None, "simple"} or explode not in {None, False}
        ):
            raise ValueError("unsupported path parameter serialization")
        if location == "query" and (
            style not in {None, "form"} or explode not in {None, True}
        ):
            raise ValueError("unsupported query parameter serialization")
        if location == "header" and (
            style not in {None, "simple"} or explode not in {None, False}
        ):
            raise ValueError("unsupported header parameter serialization")
        if parameter.get("description") and "description" not in schema:
            schema["description"] = parameter["description"]
        grouped[location].append(
            {
                "name": name,
                "schema": schema,
                "required": bool(parameter.get("required")),
            }
        )

    if any(not item["required"] for item in grouped["path"]):
        raise ValueError("path parameters must be required")

    body_content_type: str | None = None
    body_schema: dict[str, Any] | None = None
    request_body = _resolve_local_refs(document, operation.get("requestBody") or {})
    if request_body:
        content = request_body.get("content") or {}
        for candidate in (
            "application/json",
            "application/merge-patch+json",
            "application/x-www-form-urlencoded",
        ):
            if candidate in content:
                body_content_type = candidate
                body_schema = copy.deepcopy(content[candidate].get("schema") or {})
                break
        if body_content_type is None:
            raise ValueError("unsupported request body encoding")
        if body_content_type == "application/x-www-form-urlencoded":
            assert body_schema is not None
            if body_schema.get("type") != "object" or any(
                not _simple_schema(value, allow_array=True)
                for value in (body_schema.get("properties") or {}).values()
            ):
                raise ValueError("unsupported form body schema")

    properties: dict[str, Any] = {}
    required_sections: list[str] = []
    for section in ("path", "query", "header"):
        if grouped[section]:
            properties[section] = _section_schema(grouped[section])
            if any(item["required"] for item in grouped[section]):
                required_sections.append(section)
    if body_schema is not None:
        properties["body"] = body_schema
        if request_body.get("required"):
            required_sections.append("body")
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required_sections:
        input_schema["required"] = required_sections
    Draft202012Validator.check_schema(input_schema)

    app_id = str(app["id"])
    summary = operation.get("summary") or f"{method.upper()} {path}"
    return DynamicOperation(
        name=_normalise_name(app_id, operation.get("operationId"), method, path),
        app_id=app_id,
        app_name=str(app.get("name") or app_id),
        method=method.upper(),
        path=path,
        summary=summary,
        description=operation.get("description") or summary,
        input_schema=input_schema,
        path_params=tuple(item["name"] for item in grouped["path"]),
        query_params=tuple(item["name"] for item in grouped["query"]),
        header_params=tuple(item["name"] for item in grouped["header"]),
        body_content_type=body_content_type,
    )


class PersonalDynamicCatalogue:
    """Per-server last-complete operation catalogue for one configured account."""

    def __init__(
        self,
        settings: Settings,
        client_factory: Callable[[], httpx2.AsyncClient] | None = None,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory or self._default_client
        self._state = DynamicCatalogueState()
        self._attempted = False
        self._lock = anyio.Lock()

    @property
    def state(self) -> DynamicCatalogueState:
        return self._state

    def _default_client(self) -> httpx2.AsyncClient:
        if (
            not self._settings.nextcloud_username
            or not self._settings.nextcloud_password
        ):
            raise RuntimeError(
                "Bridgette Personal requires configured account credentials"
            )
        return nextcloud_httpx_client(
            auth=httpx2.BasicAuth(
                self._settings.nextcloud_username,
                self._settings.nextcloud_password,
            ),
            timeout=httpx2.Timeout(self._settings.dynamic_api_timeout_seconds),
        )

    @property
    def _viewer_url(self) -> str:
        return (
            f"{(self._settings.nextcloud_host or '').rstrip('/')}/apps/ocs_api_viewer"
        )

    async def _bounded_json(self, client: httpx2.AsyncClient, url: str) -> Any:
        async with client.stream(
            "GET",
            url,
            headers={"Accept": "application/json"},
        ) as response:
            response.raise_for_status()
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > _MAX_DOCUMENT_BYTES:
                    raise ValueError("discovery document exceeds byte limit")
                chunks.append(chunk)
        return httpx2.Response(200, content=b"".join(chunks)).json()

    async def ensure(self) -> DynamicCatalogueState:
        async with self._lock:
            if self._attempted:
                return self._state
        return await self.refresh()

    async def refresh(self) -> DynamicCatalogueState:
        async with self._lock:
            errors: list[dict[str, str]] = []
            operations: dict[str, DynamicOperation] = {}
            apps_result: list[dict[str, Any]] = []
            try:
                with anyio.fail_after(_DISCOVERY_DEADLINE_SECONDS):
                    async with self._client_factory() as client:
                        apps_payload = _unwrap_ocs(
                            await self._bounded_json(client, f"{self._viewer_url}/apps")
                        )
                        if isinstance(apps_payload, dict):
                            apps_payload = apps_payload.get("apps", [])
                        if not isinstance(apps_payload, list):
                            raise ValueError("apps response is not a list")
                        if len(apps_payload) > _MAX_APPS:
                            raise ValueError("installed app count exceeds limit")
                        for raw_app in apps_payload:
                            if not isinstance(raw_app, dict) or not raw_app.get("id"):
                                continue
                            app = {
                                "id": str(raw_app["id"]),
                                "name": raw_app.get("name"),
                            }
                            app_errors = 0
                            count = 0
                            try:
                                document = _unwrap_ocs(
                                    await self._bounded_json(
                                        client,
                                        f"{self._viewer_url}/apps/{quote(app['id'], safe='')}",
                                    )
                                )
                                if not isinstance(document, dict):
                                    raise ValueError(
                                        "OpenAPI response is not an object"
                                    )
                                for path, path_item in (
                                    document.get("paths") or {}
                                ).items():
                                    if (
                                        not isinstance(path, str)
                                        or not path.startswith("/")
                                        or path.startswith("//")
                                        or not isinstance(path_item, dict)
                                    ):
                                        continue
                                    inherited = path_item.get("parameters") or []
                                    for method, raw_operation in path_item.items():
                                        if (
                                            method not in _HTTP_METHODS
                                            or not isinstance(raw_operation, dict)
                                        ):
                                            continue
                                        try:
                                            definition = _build_operation(
                                                app,
                                                document,
                                                path,
                                                method,
                                                raw_operation,
                                                inherited,
                                            )
                                            base_name = definition.name
                                            suffix = 0
                                            while definition.name in operations:
                                                suffix += 1
                                                digest = hashlib.sha256(
                                                    f"{method}:{path}:{suffix}".encode()
                                                ).hexdigest()[:8]
                                                definition = replace(
                                                    definition,
                                                    name=f"{base_name}_{digest}",
                                                )
                                            if len(operations) >= _MAX_OPERATIONS:
                                                raise RuntimeError(
                                                    "operation count exceeds limit"
                                                )
                                            operations[definition.name] = definition
                                            count += 1
                                        except (
                                            AttributeError,
                                            KeyError,
                                            SchemaError,
                                            TypeError,
                                            ValueError,
                                        ) as exc:
                                            app_errors += 1
                                            logger.debug(
                                                "Skipping unsupported Personal operation %s %s (%s)",
                                                method.upper(),
                                                path,
                                                _error_summary(exc),
                                            )
                                apps_result.append(
                                    {
                                        "id": app["id"],
                                        "name": app.get("name") or app["id"],
                                        "operation_count": count,
                                        "unsupported_operation_count": app_errors,
                                    }
                                )
                            except Exception as exc:
                                errors.append(
                                    {"app_id": app["id"], "error": _error_summary(exc)}
                                )
            except Exception as exc:
                errors.append(
                    {"app_id": "ocs_api_viewer", "error": _error_summary(exc)}
                )

            attempted_at = _now()
            if errors:
                self._state = replace(
                    self._state,
                    complete=False,
                    stale=bool(self._state.operations),
                    last_attempt=attempted_at,
                    errors=tuple(errors),
                )
            else:
                self._state = DynamicCatalogueState(
                    operations=operations,
                    apps=tuple(sorted(apps_result, key=lambda item: item["id"])),
                    complete=True,
                    stale=False,
                    generation=self._state.generation + 1,
                    last_attempt=attempted_at,
                    last_successful_refresh=attempted_at,
                    errors=(),
                )
            self._attempted = True
            return self._state

    async def status(self, refresh: bool = False) -> dict[str, Any]:
        state = await (self.refresh() if refresh else self.ensure())
        return {
            "edition": "personal",
            "complete": state.complete,
            "stale": state.stale,
            "generation": state.generation,
            "app_count": len(state.apps),
            "operation_count": len(state.operations),
            "apps": state.apps,
            "last_attempt": state.last_attempt,
            "last_successful_refresh": state.last_successful_refresh,
            "errors": state.errors,
        }

    async def search(
        self,
        query: str = "",
        app_id: str = "",
        method: str = "",
        limit: int = 20,
        refresh: bool = False,
    ) -> dict[str, Any]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        state = await (self.refresh() if refresh else self.ensure())
        query_value = query.casefold().strip()
        app_value = app_id.casefold().strip()
        method_value = method.upper().strip()
        matches = []
        for operation in sorted(state.operations.values(), key=lambda item: item.name):
            haystack = " ".join(
                (
                    operation.name,
                    operation.app_id,
                    operation.app_name,
                    operation.summary,
                    operation.description,
                    operation.path,
                )
            ).casefold()
            if query_value and query_value not in haystack:
                continue
            if app_value and operation.app_id.casefold() != app_value:
                continue
            if method_value and operation.method != method_value:
                continue
            matches.append(operation.summary_payload())
        return {
            "complete": state.complete,
            "stale": state.stale,
            "generation": state.generation,
            "total_matches": len(matches),
            "returned": min(len(matches), limit),
            "operations": matches[:limit],
            "discovery_errors": state.errors,
        }

    async def call(
        self,
        operation_name: str,
        arguments: dict[str, Any],
        *,
        allow_stale: bool = False,
    ) -> dict[str, Any]:
        state = await self.ensure()
        if not state.complete and not (state.stale and allow_stale):
            raise RuntimeError(
                "Live API catalogue is incomplete; refresh or explicitly allow stale use"
            )
        operation = state.operations.get(operation_name)
        if operation is None:
            raise ValueError(f"Unknown live operation {operation_name!r}; search first")
        try:
            Draft202012Validator(
                operation.input_schema,
                format_checker=FormatChecker(),
            ).validate(arguments)
        except ValidationError as exc:
            location = ".".join(str(item) for item in exc.absolute_path) or "arguments"
            raise ValueError(
                f"Invalid operation arguments at {location}: {exc.message}"
            ) from None

        path_values = arguments.get("path", {})
        path = operation.path
        for name in operation.path_params:
            value = str(path_values[name])
            if value in {".", ".."}:
                raise ValueError("Operation path could not be safely resolved")
            path = path.replace(f"{{{name}}}", quote(value, safe=""))
        if (
            "{" in path
            or "}" in path
            or not path.startswith("/")
            or path.startswith("//")
        ):
            raise ValueError("Operation path could not be safely resolved")
        query_values = arguments.get("query", {})
        query: list[tuple[str, Any]] = []
        for name in operation.query_params:
            value = query_values.get(name)
            if isinstance(value, list):
                query.extend((name, item) for item in value)
            elif value is not None:
                query.append((name, value))
        headers = {"Accept": "application/json, */*"}
        for name, value in arguments.get("header", {}).items():
            if name.casefold() in _BLOCKED_HEADERS:
                raise ValueError("Protected request header refused")
            headers[name] = str(value)

        body = arguments.get("body")
        json_body = form_body = None
        if body is not None:
            if operation.body_content_type and "json" in operation.body_content_type:
                json_body = body
            elif operation.body_content_type == "application/x-www-form-urlencoded":
                form_body = body
            else:
                raise ValueError("Unsupported request body encoding")
            headers["Content-Type"] = operation.body_content_type

        url = f"{(self._settings.nextcloud_host or '').rstrip('/')}{path}"
        async with self._client_factory() as client:
            async with client.stream(
                operation.method,
                url,
                params=query,
                headers=headers,
                json=json_body,
                data=form_body,
            ) as response:
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > _MAX_RESPONSE_BYTES:
                        raise ValueError("Dynamic API response exceeds byte limit")
                    chunks.append(chunk)
                content = b"".join(chunks)
                status_code = response.status_code
                response_headers = {
                    name: value
                    for name, value in response.headers.items()
                    if name.casefold() in _SAFE_RESPONSE_HEADERS
                }
                response_content_type = response.headers.get(
                    "content-type", ""
                ).casefold()

        payload: dict[str, Any] = {
            "ok": 200 <= status_code < 300,
            "status_code": status_code,
            "operation": operation.name,
            "app_id": operation.app_id,
            "method": operation.method,
            "path": operation.path,
            "catalogue_generation": state.generation,
            "used_stale_catalogue": state.stale,
            "response_headers": response_headers,
            "response_bytes": len(content),
        }
        if "json" in response_content_type:
            try:
                payload["data"] = httpx2.Response(200, content=content).json()
            except ValueError:
                payload["data"] = content.decode("utf-8", errors="replace")
        elif (
            response_content_type.startswith("text/") or "xml" in response_content_type
        ):
            payload["data"] = content.decode("utf-8", errors="replace")
        else:
            payload["data_base64"] = base64.b64encode(content).decode("ascii")
            payload["encoding"] = "base64"
        return payload


def configure_personal_dynamic_tools(
    mcp: MCPServer,
    *,
    catalogue: PersonalDynamicCatalogue | None = None,
) -> PersonalDynamicCatalogue:
    """Register three Personal-only tools and return their per-server catalogue."""
    bound = catalogue or PersonalDynamicCatalogue(get_settings())

    @mcp.tool(
        name="nc_dynamic_discovery_status",
        title="Check Dynamic API Discovery",
        description="Report or refresh the bounded live installed-app API catalogue.",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True),
    )
    async def nc_dynamic_discovery_status(refresh: bool = False) -> dict[str, Any]:
        return await bound.status(refresh=refresh)

    @mcp.tool(
        name="nc_dynamic_search_operations",
        title="Search Dynamic API Operations",
        description="Search live operations and return the exact schema for nc_dynamic_call.",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True),
    )
    async def nc_dynamic_search_operations(
        query: str = "",
        app_id: str = "",
        method: str = "",
        limit: int = 20,
        refresh: bool = False,
    ) -> dict[str, Any]:
        return await bound.search(query, app_id, method, limit, refresh)

    @mcp.tool(
        name="nc_dynamic_call",
        title="Call Dynamic API Operation",
        description=(
            "Execute one searched operation against the configured Personal account. "
            "Prefer curated nc_* tools. Stale catalogue use must be explicitly requested."
        ),
        annotations=ToolAnnotations(
            read_only_hint=False,
            idempotent_hint=False,
            open_world_hint=True,
        ),
    )
    async def nc_dynamic_call(
        operation: str,
        arguments: dict[str, Any] | None = None,
        allow_stale: bool = False,
    ) -> dict[str, Any]:
        return await bound.call(operation, arguments or {}, allow_stale=allow_stale)

    return bound
