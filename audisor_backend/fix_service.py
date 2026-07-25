"""Minimal Fix service entrypoint for container deployment.

Commands:
    accept <path>   Process one Fix operation from an envelope JSON file.
    health          Print status JSON and exit 0 if the engine is constructible.

This module is independent of the tombstoned audisor CLI and can be invoked
directly via: python -m audisor_backend.fix_service <command>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def health() -> int:
    """Prove the Fix engine can be constructed."""
    try:
        from audisor_backend.controllers.fix_host import AcceptedFixDispatcher  # noqa: F401
        import audisor_backend

        version = getattr(audisor_backend, "__version__", "0.0.0")
        print(json.dumps({"status": "healthy", "version": version}))
        return 0
    except Exception as exc:
        print(
            json.dumps({"status": "unhealthy", "error": f"{type(exc).__name__}: {exc}"}),
            file=sys.stderr,
        )
        return 1


def accept(envelope_path: str) -> int:
    """Process one Fix operation from an envelope file.

    The envelope is a JSON file containing the operation specification
    that the AcceptedFixDispatcher expects.
    """
    path = Path(envelope_path)
    if not path.exists():
        print(
            json.dumps({"error": "envelope_not_found", "path": str(path)}),
            file=sys.stderr,
        )
        return 1

    try:
        envelope: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(
            json.dumps({"error": "envelope_parse_error", "message": str(exc)}),
            file=sys.stderr,
        )
        return 1

    try:
        from audisor_backend.controllers.fix_controller import FixController

        controller = FixController()
        result = controller.accept(envelope)
        print(json.dumps(result, default=str))
        return 0
    except Exception as exc:
        print(
            json.dumps({"error": type(exc).__name__, "message": str(exc)}),
            file=sys.stderr,
        )
        return 2


def main(argv: list[str] | None = None) -> int:
    """Entry point for the Fix service."""
    args = argv if argv is not None else sys.argv[1:]

    if not args:
        print("Usage: fix_service [accept <path> | health]", file=sys.stderr)
        return 1

    command = args[0]

    if command == "health":
        return health()

    if command == "accept":
        if len(args) < 2:
            print("Usage: fix_service accept <envelope_path>", file=sys.stderr)
            return 1
        return accept(args[1])

    print(f"Unknown command: {command}", file=sys.stderr)
    print("Usage: fix_service [accept <path> | health]", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
