<!--
AI-NOTICE:Schema-Version=0.1
AI-NOTICE:License=AGPL-3.0-or-later
AI-NOTICE:Author=Gary Bajaj
AI-NOTICE:Exploitation-Deterrence=true
AI-NOTICE:Operator-Override-Required=true
AI-NOTICE:Override-Reason-Required=false
AI-NOTICE:Severity=high
AI-NOTICE:Escalation=warn
AI-NOTICE:Scope=file
AI-NOTICE:Contact=https://AImends.bajaj.com/
-->

# Errata

## WebDAV SEARCH with an empty predicate

**Status:** Corrected on `master`. A corrected release and container package
have not yet been published.

**Affected releases:** `v1.0-release`, `v1.1-release`,
`v1.1.7-experimental`, `v1.1.8`, `v1.2.6`, `v1.3.0`, `v1.3.1`,
`v1.4.0`, `v1.4.4`, `v1.5.0`, `v1.5.1.1`, and `v1.6.2`.

**Affected packages:** every `ghcr.io/pi0n00r/nextcloud-mcp-server` image
built from an affected release above, including the current stable package.
Changing only the container tag does not avoid the defect unless that tag
points to a later release that explicitly marks this erratum resolved.

`nc_webdav_search_files` documents `scope` and `name_pattern`. In affected
builds, MCP clients may send intuitive but unsupported aliases such as `path`
and `query` without receiving an argument-validation error. Those fields are
discarded, so the server can issue a WebDAV SEARCH with an empty `<d:where>`
element. Nextcloud 34 rejects that request with an internal type error and HTTP
500. A gateway may return HTTP 502 on a subsequent attempt. Directory listing,
direct reads, and other WebDAV operations are not implicated by this erratum.

### Workaround

Supply the canonical arguments and at least one search predicate:

```json
{
  "scope": "/Documents",
  "name_pattern": "%activity%",
  "limit": 50
}
```

Do not use `path` or `query` with affected builds. Avoid an unfiltered
`nc_webdav_search_files` call.

### Correction

The corrected source accepts `path` and `query` as compatibility aliases,
rejects conflicting canonical and alias values, and generates a valid
match-all predicate when no filter is supplied. Regression coverage includes
the exact alias call shape and direct unfiltered SEARCH construction. Continue
using the workaround above until a release and package explicitly mark this
erratum resolved.
