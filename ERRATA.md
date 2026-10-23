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

## Login Flow v2 grant ownership

**BasicAuth is *not* affected.**

**Status:** Corrected on `master` in application version `0.185.1`. A
corrected container package has not yet been published.

**Affected releases and packages:** builds containing application version
`0.185.0` or earlier when configured for OAuth with Nextcloud Login Flow v2.
These affected builds expose the vulnerable provisioning path only when that
optional mode is enabled.

In affected Login Flow v2 deployments, an authenticated caller can start a
provisioning flow and give its approval URL to another Nextcloud user. If that
user grants access, the resulting app password can be stored for the caller
who started the flow rather than the account that approved it. This is the
cross-user credential-confusion issue tracked as
[GHSA-84qv-22q6-x82r](https://github.com/cbcoutinho/nextcloud-mcp-server/security/advisories/GHSA-84qv-22q6-x82r).

### Operator action

Do not enable or resume Login Flow v2 on an affected build. Upgrade to a build
containing application version `0.185.1` or later. Existing grants created by
an affected build cannot be attributed retroactively, so operators should
purge stored Login Flow app-password records, revoke the corresponding device
tokens in Nextcloud, and re-provision users after upgrading.

The correction resolves the approving account through Nextcloud and compares
its canonical UID with the authenticated caller before storing the credential.
A mismatch or unverifiable grant is rejected and the newly issued app password
is revoked.

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
