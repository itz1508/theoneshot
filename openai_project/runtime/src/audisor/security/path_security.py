"""Repository path security for Audisor operations.

All paths must be canonicalized, validated against traversal attacks,
and checked against the authority scope before any filesystem operation.
"""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Iterable


class PathSecurityError(ValueError):
    """Raised when a path violates security constraints."""


# Windows reserved device names
WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


# Dangerous path patterns
DANGEROUS_PATTERNS = [
    re.compile(r"^\.\.$"),  # Parent directory reference
    re.compile(r"^\.$"),    # Current directory reference (context-dependent)
    re.compile(r"\.\."),    # Any parent traversal
    re.compile(r"^\\"),     # UNC path start
    re.compile(r"^/"),     # Absolute Unix path
    re.compile(r"^[a-zA-Z]:"),  # Windows drive letter
    re.compile(r"^\\\\"),  # UNC path
    re.compile(r"^//"),    # Double slash
    re.compile(r"\x00"),   # Null byte
]


def is_windows_reserved(name: str) -> bool:
    """Check if a filename is a reserved Windows device name."""
    base = name.split(".", 1)[0].casefold()
    return base in WINDOWS_RESERVED


def validate_relative_path(path: str, *, allow_dot: bool = False) -> str:
    """Validate a platform-neutral relative path.

    Args:
        path: The path string to validate.
        allow_dot: If True, "." is allowed as a valid path.

    Returns:
        The validated path string.

    Raises:
        PathSecurityError: If the path is absolute, contains traversal,
            null bytes, or other dangerous patterns.
    """
    if not isinstance(path, str) or not path:
        raise PathSecurityError("path must be a non-empty string")

    if "\x00" in path:
        raise PathSecurityError("path contains null byte")

    if allow_dot and path == ".":
        return path

    # Check for absolute paths
    windows = PureWindowsPath(path)
    posix = PurePosixPath(path.replace("\\", "/"))

    if windows.drive or windows.root or posix.is_absolute():
        raise PathSecurityError("path must be relative, not absolute")

    if ":" in path and not path.startswith(("http:", "https:", "file:")):
        raise PathSecurityError("path contains colon (possible drive letter)")

    # Check traversal
    parts = [p for p in posix.parts if p]
    if not parts:
        raise PathSecurityError("path is empty after normalization")

    for part in parts:
        if part in (".", ".."):
            raise PathSecurityError(f"path contains traversal component: {part}")
        if part.endswith((".", " ")):
            raise PathSecurityError(f"path component ends with dot or space: {part}")
        if is_windows_reserved(part):
            raise PathSecurityError(f"path component is Windows reserved name: {part}")

    # Check dangerous patterns
    for pattern in DANGEROUS_PATTERNS:
        if pattern.search(path):
            # Re-check allow_dot for the specific "." case
            if allow_dot and path == ".":
                continue
            if pattern.pattern == r"^\.\.$":
                raise PathSecurityError("path contains parent directory reference")
            if pattern.pattern == r"^\.$" and not allow_dot:
                raise PathSecurityError("path is current directory reference")
            if pattern.pattern == r"\.\.":
                raise PathSecurityError("path contains parent traversal")
            if pattern.pattern in (r"^\\", r"^/"):
                raise PathSecurityError("path is absolute")
            if pattern.pattern == r"^[a-zA-Z]:":
                raise PathSecurityError("path contains Windows drive letter")
            if pattern.pattern in (r"^\\\\", r"^//"):
                raise PathSecurityError("path is UNC/network path")
            if pattern.pattern == r"\x00":
                raise PathSecurityError("path contains null byte")

    return path


def canonicalize_path(path: str, base_dir: Path | None = None) -> Path:
    """Canonicalize a path relative to a base directory.

    Resolves symlinks and normalizes separators.  The resulting path
    is guaranteed to be within base_dir (no symlink escape).

    Args:
        path: The path to canonicalize.
        base_dir: The base directory that the path must not escape.
            Defaults to the current working directory.

    Returns:
        A resolved, absolute Path.

    Raises:
        PathSecurityError: If the path escapes base_dir via symlinks
            or traversal.
    """
    if base_dir is None:
        base_dir = Path.cwd()

    # Validate the raw path first
    validated = validate_relative_path(path, allow_dot=True)

    # Resolve to absolute path
    target = (base_dir / validated).resolve()

    # Ensure it's still within base_dir after resolution
    try:
        target.relative_to(base_dir.resolve())
    except ValueError:
        raise PathSecurityError(
            f"path escapes base directory: {target} is outside {base_dir}"
        )

    return target


def check_paths_allowed(
    paths: Iterable[str],
    allowed_paths: Iterable[str],
    prohibited_paths: Iterable[str],
    base_dir: Path | None = None,
) -> tuple[bool, str]:
    """Check if all paths are within allowed scope and not prohibited.

    Args:
        paths: The paths to check.
        allowed_paths: Allowed path prefixes.
        prohibited_paths: Prohibited path prefixes.
        base_dir: Base directory for resolution.

    Returns:
        (True, "paths authorized") if all paths pass.
        (False, reason) if any path fails.
    """
    if base_dir is None:
        base_dir = Path.cwd()

    allowed_normalized = [
        validate_relative_path(p, allow_dot=True) for p in allowed_paths
    ]
    prohibited_normalized = [
        validate_relative_path(p, allow_dot=True) for p in prohibited_paths
    ]

    for path in paths:
        try:
            canonical = canonicalize_path(path, base_dir)
        except PathSecurityError as exc:
            return False, f"path validation failed for '{path}': {exc}"

        # Check against prohibited paths
        for prohibited in prohibited_normalized:
            prohibited_canonical = canonicalize_path(prohibited, base_dir)
            if canonical == prohibited_canonical or prohibited_canonical in canonical.parents:
                return False, f"path '{path}' matches prohibited path '{prohibited}'"

        # Check against allowed paths
        allowed = False
        for allowed_path in allowed_normalized:
            allowed_canonical = canonicalize_path(allowed_path, base_dir)
            if canonical == allowed_canonical or allowed_canonical in canonical.parents:
                allowed = True
                break

        if not allowed:
            return False, f"path '{path}' is outside allowed paths"

    return True, "paths authorized"


def sanitize_path_for_display(path: str) -> str:
    """Sanitize a path for display/logging (no filesystem access).

    Replaces home directory with ~ and normalizes separators.
    """
    # Expand user home for display purposes only
    expanded = os.path.expanduser(path)
    # Normalize separators
    normalized = expanded.replace("\\", "/")
    # Collapse multiple slashes
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    return normalized