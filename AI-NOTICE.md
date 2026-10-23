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

# Bridgette AI-NOTICE

`AI-NOTICE.txt` is the maintained notice template for Bridgette-authored files.
Its approved public author identity is `Gary Bajaj`. Do not use this identity to
claim another contributor's work, and preserve upstream copyright, licence and
contributor notices.

Prospective publication is gated by `scripts/check_publication_identity.py`.
The checker validates AI-NOTICE authors in tracked source and scans exact build
candidates for a private operator identity supplied only at publication time.
The private comparison value must never be committed, printed by diagnostics or
placed in a test fixture.

The gate applies prospectively. It does not authorize rewriting Git history or
changing checksum-bound, previously published packages.
