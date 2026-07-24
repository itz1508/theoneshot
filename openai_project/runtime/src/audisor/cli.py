"""Command-line entrypoint for local Audisor setup and external A-Flow state.

In 0.10.0 the legacy BYOK/BYOM commands are tombstoned.  Deprecated
commands are intercepted before argument parsing so that missing legacy
arguments do not trigger argparse errors.  The tombstone prints a
deprecation message to stderr and exits with code 1.

Retained commands:
  audisor aflow on|off|status
  audisor integrate codex --scope repo --dry-run|--apply|--status|--remove
"""

from __future__ import annotations

import argparse
import sys

from .config import is_aflow_enabled, load_dotenv, set_aflow_enabled

_DEPRECATED_COMMANDS = {"setup", "host", "codex", "run"}

_DEPRECATED_MESSAGES: dict[str, list[str]] = {
    "setup": [
        "audisor setup is deprecated.",
        "",
        "Provider configuration is now owned by OneShot Fix or the external coding agent.",
    ],
    "host": [
        "audisor host accept is deprecated.",
        "",
        "Use the external coding agent with A-Flow MCP review for operation execution.",
    ],
    "codex": [
        "audisor codex is deprecated.",
        "",
        "Supported workflow:",
        "1. Run: audisor integrate codex --scope repo --apply",
        "2. Start Codex directly in the target repository.",
        "3. Use the aflow_review MCP tool before implementation.",
    ],
    "run": [
        "audisor run is deprecated.",
        "",
        "Use the external coding agent for task execution.",
        "A-Flow review is available via MCP.",
    ],
}


def _deprecated_tombstone(command: str, *, stderr) -> int:
    for line in _DEPRECATED_MESSAGES[command]:
        print(line, file=stderr)
    return 1


# ---------------------------------------------------------------------------
# Legacy command handlers — retained for direct Python API compatibility.
# Imports are lazy so the module loads without the legacy dependency graph.
# These functions are NOT called by the tombstone CLI path in 0.10.0.
# ---------------------------------------------------------------------------


def _setup() -> int:
    from .ollama_setup import OllamaSetupError, setup_ollama

    try:
        result = setup_ollama()
        set_aflow_enabled(True)
    except OllamaSetupError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("Audisor is ready.")
    print("\nOllama: running")
    print(f"Model: {result.model}")
    print("A-Flow: ON")
    print("Connection: verified")
    return 0


def _host_accept(args, *, service, stdin, stdout, stderr) -> int:
    from .operations.models import OperationIdentityConflict
    from .operations.transport import (
        EXIT_IDENTITY_CONFLICT,
        EXIT_INPUT_ERROR,
        EXIT_SERVICE_ERROR,
        canonical_response,
        deserialize_request,
        read_request,
        TransportError,
    )

    try:
        payload = read_request(request_file=args.request_file, stdin=stdin)
        request = deserialize_request(payload)
    except TransportError as exc:
        print(f"{exc.code}: {exc}", file=stderr)
        return EXIT_INPUT_ERROR
    except Exception:
        print("invalid_operation_envelope: request could not be deserialized", file=stderr)
        return EXIT_INPUT_ERROR
    try:
        response = service.accept(request)
    except OperationIdentityConflict as exc:
        print(f"operation_identity_conflict: {exc}", file=stderr)
        return EXIT_IDENTITY_CONFLICT
    except Exception as exc:
        print(f"service_error: {type(exc).__name__}", file=stderr)
        return EXIT_SERVICE_ERROR
    stdout.write(canonical_response(response))
    return 0


def _codex_build(args, *, stdout, stderr, adapter=None, task_runner=None) -> int:
    from .codex.adapter import CodexAdapter, CodexAdapterError

    try:
        if args.build_id:
            result = (adapter or CodexAdapter()).run(args.build_id, operation_id=args.operation_id)
        else:
            from .codex.task_entry import prepare_and_run_task
            result = (task_runner or prepare_and_run_task)(args.build, adapter_factory=(lambda: adapter) if adapter is not None else None)
    except CodexAdapterError as exc:
        print(f"Build operation failed.\nCode: {exc.code}\nCodex was not started.", file=stderr)
        return 1
    if hasattr(result, "as_dict"):
        if result.status != "accepted":
            print("Audisor stopped this Build.\nCodex was not started.", file=stderr)
            return 1
        return 0
    print(f"Prepared Build: {result.build_id}", file=stderr)
    print("Submitting governed operation...", file=stderr)
    print("Continuation: permitted", file=stderr)
    print("Starting Codex...", file=stderr)
    return result.exit_code


def _run_tasks(args, *, stderr) -> int:
    from pathlib import Path

    from audisor.schemas.task_input import TaskInputBatch

    from .audisor_run_gate import check_aflow_gate, write_failure_results
    from .file_runner import run_file_tasks
    from .routing.configuration import get_provider_router
    from .service import TaskService

    try:
        input_path = Path(args.input)
        with input_path.open("r", encoding="utf-8") as stream:
            batch = TaskInputBatch.model_validate_json(stream.read())

        gate = check_aflow_gate(batch.root)
        if not gate.permitted:
            write_failure_results(Path(args.output), batch.root, gate.reason)
            print(f"task_run_error: AFlowRejected: {gate.reason}", file=stderr)
            return 1

        run_file_tasks(
            service=TaskService(get_provider_router()),
            input_path=args.input,
            output_path=args.output,
        )
    except Exception as exc:
        print(f"task_run_error: {type(exc).__name__}", file=stderr)
        return 1
    return 0


def main(
    argv: list[str] | None = None,
    *,
    operation_service=None,
    codex_adapter=None,
    task_runner=None,
    stdin=None,
    stdout=None,
    stderr=None,
) -> int:
    load_dotenv()
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr

    raw_argv = argv if argv is not None else sys.argv[1:]

    # Integrate has its own argument parser — intercept before the main parser
    if raw_argv and raw_argv[0] == "integrate":
        from .integrate import run_integrate
        return run_integrate(raw_argv[1:])

    # Deprecated commands — intercept before argument validation so that
    # missing legacy required arguments do not trigger argparse errors.
    if raw_argv and raw_argv[0] in _DEPRECATED_COMMANDS:
        return _deprecated_tombstone(raw_argv[0], stderr=stderr)

    parser = argparse.ArgumentParser(prog="audisor")
    commands = parser.add_subparsers(dest="command", required=True)

    # Deprecated commands remain registered for --help discoverability
    commands.add_parser("setup")
    commands.add_parser("host")
    commands.add_parser("codex")
    commands.add_parser("run")

    aflow = commands.add_parser("aflow")
    aflow.add_argument("action", choices=("on", "off", "status"))

    args = parser.parse_args(argv)

    if args.command == "aflow":
        if args.action == "status":
            print(f"A-Flow: {'ON' if is_aflow_enabled() else 'OFF'}")
            return 0
        set_aflow_enabled(args.action == "on")
        return 0

    # Deprecated commands that somehow reach here (e.g. through --help)
    if args.command in _DEPRECATED_COMMANDS:
        return _deprecated_tombstone(args.command, stderr=stderr)

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
