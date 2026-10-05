#!/usr/bin/env python3
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
"""Fail prospective publication on private identity or notice-author drift."""

from __future__ import annotations

import argparse
import io
import os
import re
import subprocess
import sys
import tarfile
import unicodedata
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

APPROVED_NOTICE_AUTHORS = frozenset({"Gary Bajaj"})
PRIVATE_IDENTITY_ENV = "AI_NOTICE_OPERATOR_L1"
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_MEMBER_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_DEPTH = 3
IGNORED_TREE_PARTS = frozenset({".git", ".venv", "__pycache__"})
AUTHOR_PATTERN = re.compile(r"AI-NOTICE:" r"Author=([^\r\n<]+)")


@dataclass(frozen=True)
class Finding:
    location: str
    reason: str


class CandidateError(RuntimeError):
    """A candidate cannot be inspected safely and completely."""


def _normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _contains_private_identity(data: bytes, private_identity: str | None) -> bool:
    if private_identity is None:
        return False
    private_bytes = private_identity.encode("utf-8")
    if private_bytes in data or private_bytes.lower() in data.lower():
        return True
    text = data.decode("utf-8", errors="ignore")
    return _normalized(private_identity) in _normalized(text)


def _notice_author_findings(text: str, location: str) -> list[Finding]:
    findings: list[Finding] = []
    for match in AUTHOR_PATTERN.finditer(text):
        author = match.group(1).strip()
        if author not in APPROVED_NOTICE_AUTHORS:
            findings.append(Finding(location, "unapproved AI-NOTICE author"))
    return findings


def _scan_name(name: str, private_identity: str | None, location: str) -> list[Finding]:
    if private_identity is None:
        return []
    if _normalized(private_identity) in _normalized(name):
        return [Finding(location, "private operator identity in path")]
    return []


def _display_location(location: str, private_identity: str | None) -> str:
    if private_identity is not None and _normalized(private_identity) in _normalized(
        location
    ):
        return "<redacted candidate path>"
    return location


def _safe_member_name(name: str) -> bool:
    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts


def _scan_blob(
    data: bytes,
    location: str,
    private_identity: str | None,
    *,
    depth: int = 0,
) -> list[Finding]:
    findings: list[Finding] = []
    if _contains_private_identity(data, private_identity):
        findings.append(Finding(location, "private operator identity in content"))

    text = data.decode("utf-8", errors="ignore")
    findings.extend(_notice_author_findings(text, location))

    if depth >= MAX_ARCHIVE_DEPTH:
        return findings

    stream = io.BytesIO(data)
    if zipfile.is_zipfile(stream):
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            for member in archive.infolist():
                nested_location = f"{location}!/{member.filename}"
                display_location = _display_location(nested_location, private_identity)
                if not _safe_member_name(member.filename):
                    raise CandidateError(f"unsafe archive member path in {location}")
                findings.extend(
                    _scan_name(member.filename, private_identity, display_location)
                )
                if member.is_dir():
                    continue
                if member.file_size > MAX_ARCHIVE_MEMBER_BYTES:
                    raise CandidateError(
                        f"archive member exceeds inspection limit in {location}"
                    )
                nested = archive.read(member)
                findings.extend(
                    _scan_blob(
                        nested,
                        display_location,
                        private_identity,
                        depth=depth + 1,
                    )
                )
        return findings

    stream.seek(0)
    try:
        archive = tarfile.open(fileobj=stream, mode="r:*")
    except (OSError, tarfile.TarError):
        return findings
    with archive:
        for member in archive.getmembers():
            nested_location = f"{location}!/{member.name}"
            display_location = _display_location(nested_location, private_identity)
            if not _safe_member_name(member.name):
                raise CandidateError(f"unsafe archive member path in {location}")
            findings.extend(_scan_name(member.name, private_identity, display_location))
            if member.issym() or member.islnk():
                findings.extend(
                    _scan_name(member.linkname, private_identity, display_location)
                )
                continue
            if not member.isfile():
                continue
            if member.size > MAX_ARCHIVE_MEMBER_BYTES:
                raise CandidateError(
                    f"archive member exceeds inspection limit in {location}"
                )
            extracted = archive.extractfile(member)
            if extracted is None:
                raise CandidateError(f"archive member could not be read in {location}")
            nested = extracted.read(MAX_ARCHIVE_MEMBER_BYTES + 1)
            if len(nested) > MAX_ARCHIVE_MEMBER_BYTES:
                raise CandidateError(
                    f"archive member exceeds inspection limit in {location}"
                )
            findings.extend(
                _scan_blob(
                    nested,
                    display_location,
                    private_identity,
                    depth=depth + 1,
                )
            )
    return findings


def _read_candidate(path: Path) -> bytes:
    size = path.lstat().st_size
    if size > MAX_FILE_BYTES:
        raise CandidateError(f"candidate exceeds inspection limit: {path}")
    if path.is_symlink():
        return os.readlink(path).encode("utf-8")
    return path.read_bytes()


def _scan_file(path: Path, label: str, private_identity: str | None) -> list[Finding]:
    display_label = _display_location(label, private_identity)
    findings = _scan_name(label, private_identity, display_label)
    # git ls-files reports gitlinks as tracked paths, but their content belongs
    # to the referenced repository and is not part of this publication tree.
    if path.is_dir():
        return findings
    findings.extend(_scan_blob(_read_candidate(path), display_label, private_identity))
    return findings


def _tracked_files(root: Path) -> list[tuple[Path, str]]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    paths = [item for item in result.stdout.split(b"\0") if item]
    return [(root / os.fsdecode(item), f"source:{os.fsdecode(item)}") for item in paths]


def _candidate_files(path: Path) -> Iterable[tuple[Path, str]]:
    if not path.exists() and not path.is_symlink():
        raise CandidateError(f"candidate does not exist: {path}")
    if path.is_file() or path.is_symlink():
        yield path, f"candidate:{path}"
        return
    for entry in sorted(path.rglob("*")):
        relative = entry.relative_to(path)
        if any(part in IGNORED_TREE_PARTS for part in relative.parts):
            continue
        if entry.is_file() or entry.is_symlink():
            yield entry, f"candidate:{path}/{relative}"


def _private_identity(required: bool) -> str | None:
    value = os.environ.get(PRIVATE_IDENTITY_ENV)
    if value is None or not value.strip():
        if required:
            raise CandidateError("private identity comparison is unavailable")
        return None
    value = value.strip()
    if value in APPROVED_NOTICE_AUTHORS:
        raise CandidateError("private identity comparison is not distinct")
    return value


def inspect(
    *,
    git_tree: Path | None,
    candidates: Iterable[Path],
    require_private_comparison: bool,
) -> tuple[list[Finding], int]:
    private_identity = _private_identity(require_private_comparison)
    files: list[tuple[Path, str]] = []
    if git_tree is not None:
        files.extend(_tracked_files(git_tree.resolve()))
    for candidate in candidates:
        files.extend(_candidate_files(candidate.resolve()))

    if not files:
        raise CandidateError("no source tree or publication candidate supplied")

    findings: list[Finding] = []
    seen: set[tuple[Path, str]] = set()
    for path, label in files:
        key = (path, label)
        if key in seen:
            continue
        seen.add(key)
        findings.extend(_scan_file(path, label, private_identity))
    return findings, len(seen)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--git-tree",
        type=Path,
        help="scan exactly the tracked files in this Git worktree",
    )
    parser.add_argument(
        "--candidate",
        type=Path,
        action="append",
        default=[],
        help="scan a generated file, archive or directory; repeat as needed",
    )
    parser.add_argument(
        "--require-private-comparison",
        action="store_true",
        help="fail closed unless the private comparison environment is present",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        findings, count = inspect(
            git_tree=args.git_tree,
            candidates=args.candidate,
            require_private_comparison=args.require_private_comparison,
        )
    except (
        CandidateError,
        OSError,
        subprocess.CalledProcessError,
        tarfile.TarError,
        zipfile.BadZipFile,
    ) as error:
        print(f"publication identity gate error: {error}", file=sys.stderr)
        return 2

    if findings:
        for finding in sorted(
            set(findings), key=lambda item: (item.location, item.reason)
        ):
            print(f"FAIL {finding.location}: {finding.reason}", file=sys.stderr)
        print(
            f"publication identity gate: {len(findings)} finding(s)",
            file=sys.stderr,
        )
        return 1

    comparison = "private+public" if os.environ.get(PRIVATE_IDENTITY_ENV) else "public"
    print(f"publication identity gate: {count} file(s) pass ({comparison} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
