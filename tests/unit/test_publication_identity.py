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

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from scripts import check_publication_identity as gate

pytestmark = pytest.mark.unit
SYNTHETIC_PRIVATE_IDENTITY = "Example Operator Private Name"


def _set_private(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(gate.PRIVATE_IDENTITY_ENV, SYNTHETIC_PRIVATE_IDENTITY)


def test_public_author_and_unrelated_contributor_notice_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_private(monkeypatch)
    candidate = tmp_path / "candidate.txt"
    candidate.write_text(
        "AI-NOTICE:" + "Author=Gary Bajaj\nCopyright 2026 Another Contributor\n",
        encoding="utf-8",
    )

    findings, count = gate.inspect(
        git_tree=None,
        candidates=[candidate],
        require_private_comparison=True,
    )

    assert findings == []
    assert count == 1


def test_private_identity_is_rejected_without_echoing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_private(monkeypatch)
    candidate = tmp_path / "README.md"
    candidate.write_text(SYNTHETIC_PRIVATE_IDENTITY, encoding="utf-8")

    result = gate.main(
        [
            "--candidate",
            str(candidate),
            "--require-private-comparison",
        ]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert "private operator identity in content" in captured.err
    assert SYNTHETIC_PRIVATE_IDENTITY not in captured.err


def test_private_identity_inside_zip_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_private(monkeypatch)
    archive_path = tmp_path / "candidate.whl"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("package/metadata.txt", SYNTHETIC_PRIVATE_IDENTITY)

    findings, _ = gate.inspect(
        git_tree=None,
        candidates=[archive_path],
        require_private_comparison=True,
    )

    assert any("!/package/metadata.txt" in item.location for item in findings)


def test_private_identity_inside_tar_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_private(monkeypatch)
    archive_path = tmp_path / "candidate.tar.gz"
    payload = SYNTHETIC_PRIVATE_IDENTITY.encode()
    with tarfile.open(archive_path, "w:gz") as archive:
        info = tarfile.TarInfo("package/NOTICE")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))

    findings, _ = gate.inspect(
        git_tree=None,
        candidates=[archive_path],
        require_private_comparison=True,
    )

    assert any("!/package/NOTICE" in item.location for item in findings)


def test_unapproved_notice_author_is_rejected_without_private_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(gate.PRIVATE_IDENTITY_ENV, raising=False)
    candidate = tmp_path / "source.py"
    candidate.write_text(
        "# AI-NOTICE:" + "Author=Unapproved Example\n",
        encoding="utf-8",
    )

    findings, _ = gate.inspect(
        git_tree=None,
        candidates=[candidate],
        require_private_comparison=False,
    )

    assert [item.reason for item in findings] == ["unapproved AI-NOTICE author"]


def test_publication_mode_fails_closed_without_private_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(gate.PRIVATE_IDENTITY_ENV, raising=False)
    candidate = tmp_path / "source.py"
    candidate.write_text("safe", encoding="utf-8")

    with pytest.raises(gate.CandidateError, match="comparison is unavailable"):
        gate.inspect(
            git_tree=None,
            candidates=[candidate],
            require_private_comparison=True,
        )


def test_private_identity_in_archive_member_name_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _set_private(monkeypatch)
    archive_path = tmp_path / "candidate.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(f"docs/{SYNTHETIC_PRIVATE_IDENTITY}.txt", "safe")

    result = gate.main(
        ["--candidate", str(archive_path), "--require-private-comparison"]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert "private operator identity in path" in captured.err
    assert SYNTHETIC_PRIVATE_IDENTITY not in captured.err
