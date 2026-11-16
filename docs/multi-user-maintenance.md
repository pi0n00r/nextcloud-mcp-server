# Multi-user maintenance

## Status

Multi-user is a community-supported compatibility track. The project is seeking
a maintainer to own it. Personal remains the maintained default edition and its
release gate is independent of Multi-user compatibility failures.

This is a maintenance boundary, not a removal notice. Existing Multi-user
BasicAuth, Login Flow, OAuth/OIDC, per-user provisioning, scope filtering and
storage behavior remain in the repository and continue to receive CI coverage.

## Maintainer scope

The Multi-user maintainer owns:

- authentication and identity flows specific to multiple users;
- per-user credential storage, encryption, migrations and recovery;
- scope authorization, tenant isolation and provisioning;
- Multi-user BasicAuth, Login Flow, OAuth/OIDC, LDAP and Keycloak test lanes;
- issue triage, documentation and release notes for those paths; and
- coordination when a shared-core change affects both editions.

The Personal maintainers retain the shared MCP/Nextcloud client contracts,
curated application tools, Personal live discovery, Personal packaging and the
Personal release decision.

## Merge and release boundary

Shared lint, unit and package checks remain common because both editions use the
same core. Personal integration is release-blocking. Multi-user integration
continues to run and its failures remain visible, but those lanes are explicitly
non-blocking for a Personal release.

A Multi-user change must not:

- alter Personal defaults or require Personal credentials to use a multi-user
  store;
- import Multi-user-only dependencies during Personal startup;
- expose Personal dynamic tools in a Multi-user tool list; or
- weaken authentication, scope or tenant boundaries to preserve compatibility.

## Extraction plan

The first maintainer project is to extract Multi-user-specific behavior behind
a versioned extension interface without rewriting working authentication code.
A practical sequence is:

1. inventory imports from shared core into the current auth, provisioning and
   storage modules;
2. define the smallest interface for identity resolution, credential lookup,
   scope authorization and lifecycle hooks;
3. move Multi-user implementations behind that interface while retaining the
   existing configuration and tests;
4. publish the extension independently and test it against declared compatible
   core versions; and
5. remove the bundled compatibility implementation only after a documented
   migration period.

The interface should be driven by existing behavior and tests. It must not add a
second server, duplicate shared application tools or require Personal releases
to wait for a Multi-user package.

## Volunteering

Open an issue using the `edition: multi-user`, `help wanted` and
`maintainer wanted` labels. Include relevant experience with Python, OAuth/OIDC,
Nextcloud Login Flow or multi-tenant services and describe the first bounded
area you would take ownership of. A maintainer starts with triage and reviewed
pull requests before receiving release permissions.
