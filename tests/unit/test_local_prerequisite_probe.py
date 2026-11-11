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

"""Tests for local ingest storage and import readiness."""

import errno

from nextcloud_mcp_server.app import (
    _probe_local_ingest_storage,
    _probe_semantic_imports,
)


def test_local_storage_probe_flushes_and_cleans_up(tmp_path):
    assert _probe_local_ingest_storage(str(tmp_path)) == (True, "ok")
    assert list(tmp_path.iterdir()) == []


def test_local_storage_probe_classifies_injected_io_failure(monkeypatch, tmp_path):
    def fail(*_args, **_kwargs):
        raise OSError(errno.EIO, "/private/ingest.bin")

    monkeypatch.setattr("nextcloud_mcp_server.app.tempfile.NamedTemporaryFile", fail)
    assert _probe_local_ingest_storage(str(tmp_path)) == (
        False,
        "error: storage_unavailable",
    )


def test_semantic_import_probe_classifies_injected_dependency_failure(monkeypatch):
    def fail(module_name):
        if module_name == "huggingface_hub":
            raise ImportError("private install detail")
        return object()

    monkeypatch.setattr("nextcloud_mcp_server.app.importlib.import_module", fail)
    assert _probe_semantic_imports() == (
        False,
        "error: dependency_unavailable",
    )
