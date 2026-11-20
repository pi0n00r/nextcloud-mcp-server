# Bridgette Personal and Multi-user

Bridgette v2 is one codebase with two runtime editions. Personal is the
maintained default product. Multi-user is a community-supported compatibility
track for the historical authentication and tenancy paths while the project
seeks a dedicated maintainer. The application package remains upstream-aligned
at `0.213.3`; `v2.0.0` introduced the runtime edition boundary.

## Personal

Set `BRIDGETTE_EDITION=personal` and
`MCP_DEPLOYMENT_MODE=single_user_basic`. Personal retains every curated tool
and uses the one configured Nextcloud app-password identity. It adds only:

- `nc_dynamic_discovery_status`
- `nc_dynamic_search_operations`
- `nc_dynamic_call`

The bridge discovers APIs from the installed `ocs_api_viewer` app. It never
accepts a host or credentials from a tool caller. Paths remain relative to the
configured Nextcloud origin, protected headers are refused, and arguments are
validated against the discovered JSON Schema before a request is sent.

Discovery is bounded to 100 apps, 2,000 supported operations, 2 MiB per
OpenAPI document and a 90-second total refresh. Responses are capped at 2 MiB.
Only primitive path/header parameters, primitive or primitive-array query
parameters, JSON bodies and simple URL-encoded bodies are supported. Cookie,
multipart and custom serialization operations are omitted and counted.

A refresh is atomic: calls use only a complete catalogue generation. If a
later refresh fails, the last complete generation is retained as stale and can
be used only with `allow_stale=true`. Curated tools remain available even when
live discovery is unavailable.

## Multi-user

Set `BRIDGETTE_EDITION=multi_user`, or omit the variable. Existing BasicAuth,
OAuth/Login Flow, per-user credentials, scope filtering, provisioning and
curated tool behavior remain unchanged. The Personal module is lazy-imported,
so a Personal-only dependency or discovery failure cannot prevent Multi-user
startup. Dynamic tools never appear in Multi-user `tools/list`.

Multi-user compatibility checks continue to run on pull requests and report
failures independently. They are non-blocking for the Personal release gate.
This avoids presenting unowned authentication and tenancy failures as Personal
release failures without pretending that Multi-user is fully maintained.

Until a maintainer accepts ownership, Multi-user receives security and data-loss
fixes when practical but has no regular feature or release commitment. The
desired end state is a separately versioned extension behind a small stable
interface to the shared Nextcloud/MCP core. See
[Multi-user maintenance](multi-user-maintenance.md).

## Compatibility

The edition switch does not replace curated tools. Personal agents should use
curated tools whenever one exists because those tools carry richer contracts,
stable response models and application-specific safeguards. Dynamic execution
is a coverage path for installed APIs that Bridgette has not curated yet.
