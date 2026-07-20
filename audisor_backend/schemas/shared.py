"""Shared primitives only; workflow semantics remain in their own namespace."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class ArtifactRef:
    id: str
    sha256: str


@dataclass(frozen=True)
class FileHash:
    path: str
    sha256: str


@dataclass(frozen=True)
class ScopedManifest:
    files: tuple[str, ...]
    dependency_closure: tuple[str, ...]
    input_hash: str


@dataclass(frozen=True)
class ValidationResult:
    id: str
    passed: bool
    actual_result: str


@dataclass(frozen=True)
class SandboxRef:
    id: str
    path: str


@dataclass(frozen=True)
class ProviderMetadata:
    provider: str
    model: str
    mode: str


@dataclass(frozen=True)
class ControllerState:
    operation_id: str
    phase: str


@dataclass(frozen=True)
class AuthorityState:
    model_advisory_only: bool = True
    mutation_authority: bool = False
    execution_authority: bool = False
    apply_authority: bool = False
    completion_authority: bool = False


@dataclass(frozen=True)
class ReleaseState:
    mode: str
    approved: bool = False


def hash_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def hash_files(root: str | Path, paths: tuple[str, ...] | list[str]) -> dict[str, str]:
    base = Path(root)
    return {path: hash_bytes((base / path).read_bytes()) for path in paths}


def hash_mapping(values: Mapping[str, str]) -> str:
    payload = "".join(f"{key}\0{values[key]}\n" for key in sorted(values))
    return hash_bytes(payload.encode())

