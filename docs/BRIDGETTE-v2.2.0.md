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

# Bridgette v2.2.0

This source release incorporates upstream Nextcloud MCP Server v0.221.2.
It is a minor release because it adds substantial user-facing capability:

- an MCP App document viewer with staged personal-data detection,
  preview-before-apply changes and copy-on-write output;
- vendored PDF.js and image decoders so the viewer is self-contained in the
  published image;
- bounded read-only Nextcloud Whiteboard listing and inspection tools; and
- configurable multilingual BM25 stemming, including mixed-script text.

The release also canonicalises paths before enforcing `EXCLUDED_TAGS`, hardens
the document viewer and PDF.js update path, and includes current dependency and
Nextcloud test-image maintenance. The app-only `preview_redaction` and
`save_redaction` viewer tools are replaced by `preview_changes` and
`apply_changes`; deployments that explicitly allowlist those app-only names
must update their allowlist.

The deployed Personal Edition remains independently maintained on the
v1.12.x stable line, currently v1.12.7. Publishing v2.2.0 changes neither that
deployment nor the designated v1.12.1 LKG.
