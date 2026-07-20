"""Issue-only Fix snapshot creation and deterministic hashing."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from audisor_backend.schemas.fix.models import Finding, FixScopedManifest


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class ScopedSnapshot:
    root: str
    files: tuple[str, ...]
    dependency_closure: tuple[str, ...]
    file_hashes: dict[str, str]
    snapshot_hash: str
    storage_path: str
    dependency_evidence: dict[str, list[dict[str, str]]]

    def manifest(self) -> FixScopedManifest:
        manifest = FixScopedManifest(
            list(self.files), list(self.dependency_closure), self.snapshot_hash,
            dict(self.file_hashes), {key: list(value) for key, value in self.dependency_evidence.items()}
        )
        manifest.validate()
        return manifest


def create_scoped_snapshot(
    repository_root: str | Path,
    findings: Iterable[Finding],
    dependency_closure: Iterable[str],
    output_root: str | Path,
    dependency_evidence: dict[str, list[dict[str, str]]] | None = None,
) -> ScopedSnapshot:
    """Copy and hash only finding files plus the supplied dependency closure."""
    root = Path(repository_root).resolve()
    output = Path(output_root).resolve()
    issue_files = {finding.file.replace("\\", "/") for finding in findings}
    closure = {item.replace("\\", "/") for item in dependency_closure}
    if not issue_files:
        raise ValueError("issue_snapshot_requires_findings")
    if not issue_files.issubset(closure):
        raise ValueError("issue_files_must_be_in_dependency_closure")
    file_hashes: dict[str, str] = {}
    for relative in sorted(closure):
        source = (root / relative).resolve()
        if os.path.commonpath([str(source), str(root)]) != str(root) or not source.is_file():
            raise ValueError(f"snapshot_file_unavailable:{relative}")
        data = source.read_bytes()
        file_hashes[relative] = _sha256(data)
    evidence = {key: list(value) for key, value in sorted((dependency_evidence or {}).items())}
    snapshot_payload = {"files": sorted(issue_files), "dependency_closure": sorted(closure), "file_hashes": file_hashes, "dependency_evidence": evidence}
    snapshot_hash = _sha256(_canonical(snapshot_payload))
    storage = output / snapshot_hash
    storage.mkdir(parents=True, exist_ok=False)
    try:
        for relative in sorted(closure):
            destination = storage / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / relative, destination)
        (storage / "snapshot-manifest.json").write_bytes(_canonical({**snapshot_payload, "snapshot_hash": snapshot_hash}) + b"\n")
    except Exception:
        shutil.rmtree(storage, ignore_errors=True)
        raise
    return ScopedSnapshot(str(root), tuple(sorted(issue_files)), tuple(sorted(closure)), file_hashes, snapshot_hash, str(storage), evidence)
