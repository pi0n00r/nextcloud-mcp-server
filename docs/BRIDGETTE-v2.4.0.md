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

# Bridgette v2.4.0

This source release incorporates upstream Nextcloud MCP Server v0.226.3.
It is a minor release because it adds substantial user-facing capability:

- Activity tools for listing, filtering and marking activity notifications;
- historical WebDAV version reads, richer file metadata, labels and
  checkpoints; and
- Nextcloud Mail editor mode and signature support.

The release also makes Mail indexing safe for Markdown content, fixes local
Qdrant scan consistency, accepts federated Talk messages whose optional
identifiers are absent, tightens trust-boundary input validation, and updates
the maintained Docling and Uvicorn integration bases. Bridgette's
byte-preserving CardDAV updates, chunked WebDAV writes, Personal discovery,
and Personal/Multi-user support boundaries remain intact.

The complete hosted matrix passed for Nextcloud 33 and 34, including the
Personal single-user NC34 PDF path and the named Personal release gate.

The deployed Personal Edition remains independently maintained on the
v1.12.x stable line, currently v1.12.8. Publishing v2.4.0 changes neither that
deployment nor the designated v1.12.3 LKG.
