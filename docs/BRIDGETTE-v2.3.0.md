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

# Bridgette v2.3.0

This source release incorporates upstream Nextcloud MCP Server v0.222.0.
It is a minor release because it adds a new user-facing application surface:

- 14 tools for the optional Lucarne AppAPI app, covering channels, catalogues
  and playlists; and
- dynamic Lucarne registration only when the Nextcloud app is available.

The release also corrects image-hit reranking so text retrieval remains the
primary signal while images still receive multimodal context, and includes
current dependency maintenance. Bridgette's byte-preserving CardDAV updates,
chunked WebDAV writes, Personal and Multi-user boundaries, and runtime
integration boundaries remain intact.

The deployed Personal Edition remains independently maintained on the
v1.12.x stable line, currently v1.12.8. Publishing v2.3.0 changes neither that
deployment nor the designated v1.12.3 LKG.
