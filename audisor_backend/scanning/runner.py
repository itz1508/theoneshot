from pathlib import Path
from typing import Callable

from audisor_backend.schemas.fix.models import Finding, FindingsList
from .scanner import DeterministicScanner, ScanConfig, ScanReport, scan_repository


def scan(root: str | Path, detector: Callable[[Path], list[Finding]]) -> FindingsList:
    """Run an injected, read-only detector; no manifest is produced on detector failure."""
    return list(detector(Path(root)))


__all__ = ["scan", "scan_repository", "DeterministicScanner", "ScanConfig", "ScanReport"]
