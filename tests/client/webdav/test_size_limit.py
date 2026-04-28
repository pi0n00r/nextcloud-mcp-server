"""A.2 — webdav write size-limit regression tests.

Pure-parser tests for the chunking threshold + content-size handling.
"""

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

from nextcloud_mcp_server.client.webdav import WebDAVClient


def test_chunk_threshold_and_size_constants_are_sensible():
    """Threshold should be well above the 20KB observed-failure cap and
    below the typical PHP upload_max_filesize default (8M-128M)."""
    assert WebDAVClient.CHUNK_THRESHOLD >= 64 * 1024  # > the 20KB observed bug
    assert WebDAVClient.CHUNK_THRESHOLD <= 8 * 1024 * 1024  # <= typical PHP cap
    assert WebDAVClient.CHUNK_SIZE > 0
    assert WebDAVClient.CHUNK_SIZE >= WebDAVClient.CHUNK_THRESHOLD


def test_chunk_count_calculation():
    """A 20MB write should chunk into ceil(20MB / CHUNK_SIZE) pieces."""
    size = 20 * 1024 * 1024
    expected_chunks = (size + WebDAVClient.CHUNK_SIZE - 1) // WebDAVClient.CHUNK_SIZE
    assert expected_chunks > 1, (
        "20MB should require multiple chunks regardless of chunk size choice"
    )
