"""Command-line entrypoint for local Audisor setup and external A-Flow state."""

from __future__ import annotations

import argparse
import json
import sys

from .config import is_aflow_enabled, load_dotenv, set_aflow_enabled
from .ollama_setup import OllamaSetupError, setup_ollama
from .operations.models import OperationIdentityConflict
from .operations.transport import (
    EXIT_IDENTITY_CONFLICT,
    EXIT_INPUT_ERROR,
    EXIT_SERVICE_ERROR,
    canonical_operation_service,
    canonical_response,
    default_operation_service,
    deserialize_request,
    read_request,
    TransportError,
)
from .codex.adapter import CodexAdapter, CodexAdapterError
from .file_runner import run_file_tasks
from .routing.configuration import get_provider_router
from .service import TaskService


def _setup() -> int:
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


def main(argv: list[str] | None = None, *, operation_service=None, codex_adapter=None, task_runner=None, stdin=None, stdout=None, stderr=None) -> int:
    load_dotenv()
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    # Integrate has its own argument parser — intercept before the main parser
    raw_argv = argv if argv is not None else sys.argv[1:]
    if raw_argv and raw_argv[0] == "integrate":
        from .integrate import run_integrate
        return run_integrate(raw_argv[1:])
    parser = argparse.ArgumentParser(prog="audisor")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup")
    aflow = commands.add_parser("aflow")
    aflow.add_argument("action", choices=("on", "off", "status"))
    host = commands.add_parser("host")
    host_commands = host.add_subparsers(dest="host_command", required=True)
    accept = host_commands.add_parser("accept")
    accept.add_argument("--request-file")
    codex = commands.add_parser("codex")
    build_source = codex.add_mutually_exclusive_group(required=True)
    build_source.add_argument("--build-id")
    build_source.add_argument("--build")
    codex.add_argument("--operation-id")
    run = commands.add_parser("run")
    run.add_argument("--input", default="/input/tasks.json")
    run.add_argument("--output", default="/output/results.json")
    args = parser.parse_args(argv)
    if args.command == "setup":
        return _setup()
    if args.command == "host":
        try:
            if operation_service is not None:
                service = operation_service
            else:
                service = canonical_operation_service()
        except Exception as exc:
            print(f"service_error: {type(exc).__name__}", file=stderr)
            return EXIT_SERVICE_ERROR
        return _host_accept(args, service=service, stdin=stdin, stdout=stdout, stderr=stderr)
    if args.command == "codex":
        return _codex_build(args, stdout=stdout, stderr=stderr, adapter=codex_adapter, task_runner=task_runner)
    if args.command == "run":
        try:
            from pathlib import Path

            from audisor.schemas.task_input import TaskInputBatch

            from .audisor_run_gate import check_aflow_gate, write_failure_results

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
    if args.action == "status":
        print(f"A-Flow: {'ON' if is_aflow_enabled() else 'OFF'}")
        return 0
    set_aflow_enabled(args.action == "on")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
