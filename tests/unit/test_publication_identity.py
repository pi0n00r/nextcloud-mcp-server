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
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _set_private(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    private_file = tmp_path / "private-l1"
    private_file.write_text(SYNTHETIC_PRIVATE_IDENTITY + "\n", encoding="utf-8")
    private_file.chmod(0o600)
    monkeypatch.setenv(gate.PRIVATE_IDENTITY_FILE_ENV, str(private_file))
    return private_file


def test_public_author_and_unrelated_contributor_notice_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_private(tmp_path, monkeypatch)
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
    _set_private(tmp_path, monkeypatch)
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
    _set_private(tmp_path, monkeypatch)
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
    _set_private(tmp_path, monkeypatch)
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
    monkeypatch.delenv(gate.PRIVATE_IDENTITY_FILE_ENV, raising=False)
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
    monkeypatch.delenv(gate.PRIVATE_IDENTITY_FILE_ENV, raising=False)
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
    _set_private(tmp_path, monkeypatch)
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


def test_container_publication_enables_the_in_image_private_gate() -> None:
    dockerfile = (REPOSITORY_ROOT / "Dockerfile").read_text(encoding="utf-8")
    workflow = (
        REPOSITORY_ROOT / ".github/workflows/docker-build-publish.yml"
    ).read_text(encoding="utf-8")
    dockerignore = (REPOSITORY_ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert "ARG AI_NOTICE_PUBLICATION_GATE=0" in dockerfile
    assert "--mount=type=secret,id=AI_NOTICE_PRIVATE_L1_FILE" in dockerfile
    assert "AI_NOTICE_PRIVATE_L1_FILE=/run/secrets/AI_NOTICE_PRIVATE_L1_FILE" in dockerfile
    assert "--require-private-comparison" in dockerfile
    assert "AI_NOTICE_PUBLICATION_GATE=1" in workflow
    assert "AI_NOTICE_PRIVATE_L1_FILE=${{ secrets.AI_NOTICE_PRIVATE_L1_FILE }}" in workflow
    assert "AI_NOTICE_OPERATOR_L1" not in workflow
    assert "context: ./package-context" in workflow
    assert "publication-gate-context/exact-source.tar" in workflow
    assert "context: ./publication-gate-context" in workflow
    assert "AI_NOTICE_PRIVATE_L1_VALUE" not in workflow
    assert "AI_NOTICE_OPERATOR" + "_L1=" not in workflow
    assert "!scripts/check_publication_identity.py" in dockerignore


def test_private_identity_matches_with_unicode_case_and_whitespace_folding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private_file = _set_private(tmp_path, monkeypatch)
    private_file.write_text("Ｅxample   Operator\tPrivate Name\n", encoding="utf-8")
    candidate = tmp_path / "candidate.txt"
    candidate.write_text("example operator private name", encoding="utf-8")

    findings, _ = gate.inspect(
        git_tree=None,
        candidates=[candidate],
        require_private_comparison=True,
    )

    assert [item.reason for item in findings] == [
        "private operator identity in content"
    ]


@pytest.mark.parametrize("mode", [0o400, 0o640, 0o644])
def test_private_identity_file_requires_exact_0600(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: int
) -> None:
    private_file = _set_private(tmp_path, monkeypatch)
    private_file.chmod(mode)
    candidate = tmp_path / "candidate.txt"
    candidate.write_text("safe", encoding="utf-8")

    with pytest.raises(gate.CandidateError, match="input is unsafe"):
        gate.inspect(
            git_tree=None,
            candidates=[candidate],
            require_private_comparison=True,
        )


def test_private_identity_file_rejects_relative_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private_file = _set_private(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(gate.PRIVATE_IDENTITY_FILE_ENV, private_file.name)
    candidate = tmp_path / "candidate.txt"
    candidate.write_text("safe", encoding="utf-8")

    with pytest.raises(gate.CandidateError, match="input is unsafe"):
        gate.inspect(
            git_tree=None,
            candidates=[candidate],
            require_private_comparison=True,
        )


def test_private_identity_file_rejects_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private_file = _set_private(tmp_path, monkeypatch)
    link = tmp_path / "private-l1-link"
    link.symlink_to(private_file)
    monkeypatch.setenv(gate.PRIVATE_IDENTITY_FILE_ENV, str(link))
    candidate = tmp_path / "candidate.txt"
    candidate.write_text("safe", encoding="utf-8")

    with pytest.raises(gate.CandidateError, match="input is unsafe"):
        gate.inspect(
            git_tree=None,
            candidates=[candidate],
            require_private_comparison=True,
        )


def test_private_identity_file_rejects_invalid_utf8_without_echo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    private_file = tmp_path / "private-l1"
    private_file.write_bytes(b"\xff\xfe")
    private_file.chmod(0o600)
    monkeypatch.setenv(gate.PRIVATE_IDENTITY_FILE_ENV, str(private_file))
    candidate = tmp_path / "candidate.txt"
    candidate.write_text("safe", encoding="utf-8")

    result = gate.main(
        ["--candidate", str(candidate), "--require-private-comparison"]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert "private identity comparison input is invalid" in captured.err
    assert "ff" not in captured.err.casefold()


def test_private_identity_file_rejects_whitespace_only_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private_file = tmp_path / "private-l1"
    private_file.write_text(" \t \n", encoding="utf-8")
    private_file.chmod(0o600)
    monkeypatch.setenv(gate.PRIVATE_IDENTITY_FILE_ENV, str(private_file))
    candidate = tmp_path / "candidate.txt"
    candidate.write_text("safe", encoding="utf-8")

    with pytest.raises(gate.CandidateError, match="input is invalid"):
        gate.inspect(
            git_tree=None,
            candidates=[candidate],
            require_private_comparison=True,
        )


def test_private_identity_file_rejects_generic_single_word(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private_file = tmp_path / "private-l1"
    private_file.write_text("Operator", encoding="utf-8")
    private_file.chmod(0o600)
    monkeypatch.setenv(gate.PRIVATE_IDENTITY_FILE_ENV, str(private_file))
    candidate = tmp_path / "candidate.txt"
    candidate.write_text("safe", encoding="utf-8")

    with pytest.raises(gate.CandidateError, match="input is invalid"):
        gate.inspect(
            git_tree=None,
            candidates=[candidate],
            require_private_comparison=True,
        )


def test_private_identity_file_rejects_oversized_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private_file = tmp_path / "private-l1"
    private_file.write_text("Private " + ("Identity" * 80), encoding="utf-8")
    private_file.chmod(0o600)
    monkeypatch.setenv(gate.PRIVATE_IDENTITY_FILE_ENV, str(private_file))
    candidate = tmp_path / "candidate.txt"
    candidate.write_text("safe", encoding="utf-8")

    with pytest.raises(gate.CandidateError, match="input is unsafe"):
        gate.inspect(
            git_tree=None,
            candidates=[candidate],
            require_private_comparison=True,
        )
