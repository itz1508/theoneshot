"""Bounded one-request HTTP transport in a terminable child process."""

from __future__ import annotations

import json as jsonlib
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any

import requests

RESPONSE_BODY_MAX_BYTES = 1_048_576
STDOUT_PROTOCOL_MAX_BYTES = 1_114_112
STDERR_DIAGNOSTICS_MAX_BYTES = 65_536
REAP_RESERVE_SECONDS = 2.0


class IsolatedHttpError(RuntimeError):
    code = "provider_transport_failed"

    def __init__(self, message: str, *, child_pid: int | None = None) -> None:
        super().__init__(message)
        self.child_pid = child_pid


class IsolatedHttpTimeout(IsolatedHttpError):
    code = "provider_timeout"


class IsolatedHttpResponseTooLarge(IsolatedHttpError):
    code = "provider_response_too_large"


class IsolatedHttpProtocolError(IsolatedHttpError):
    code = "provider_protocol_invalid"


class IsolatedHttpReapError(IsolatedHttpError):
    code = "provider_process_reap_failed"


class IsolatedHttpBudgetError(IsolatedHttpError):
    code = "stage_budget_exhausted"


@dataclass(frozen=True)
class IsolatedHttpResponse:
    status_code: int
    body: bytes
    child_pid: int

    def json(self) -> Any:
        return jsonlib.loads(self.body.decode("utf-8"))


def _bounded_reader(stream: Any, limit: int, sink: bytearray, overflow: threading.Event) -> None:
    try:
        while True:
            chunk = stream.read(8192)
            if not chunk:
                return
            remaining = limit - len(sink)
            if remaining > 0:
                sink.extend(chunk[:remaining])
            if len(chunk) > remaining:
                overflow.set()
                return
    finally:
        stream.close()


def _reap(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        process.wait()
        return
    process.terminate()
    try:
        process.wait(timeout=1.0)
        return
    except subprocess.TimeoutExpired:
        process.kill()
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired as exc:
        raise IsolatedHttpReapError(
            "Provider transport process could not be reaped", child_pid=process.pid
        ) from exc


def isolated_request(
    url: str,
    *,
    method: str = "POST",
    headers: dict[str, str] | None = None,
    json: Any = None,
    timeout: float,
) -> IsolatedHttpResponse:
    """Perform one HTTP request; return only after the child has exited."""
    if timeout <= REAP_RESERVE_SECONDS:
        raise IsolatedHttpBudgetError("No provider budget remains after reap reserve")
    envelope = {
        "url": url,
        "method": method,
        "headers": headers or {},
        "json": json,
        "timeout": max(0.001, timeout - REAP_RESERVE_SECONDS),
        "response_body_max_bytes": RESPONSE_BODY_MAX_BYTES,
    }
    encoded = json_module_dumps(envelope)
    process_options: dict[str, Any] = {}
    if sys.platform == "win32":
        process_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        process_options["start_new_session"] = True
    process = subprocess.Popen(
        [sys.executable, "-m", "audisor.workers.isolated_http", "--child"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        **process_options,
    )
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None
    stdout = bytearray()
    stderr = bytearray()
    stdout_overflow = threading.Event()
    stderr_overflow = threading.Event()
    out_thread = threading.Thread(
        target=_bounded_reader,
        args=(process.stdout, STDOUT_PROTOCOL_MAX_BYTES, stdout, stdout_overflow),
        name=f"provider-http-stdout-{process.pid}",
        daemon=True,
    )
    err_thread = threading.Thread(
        target=_bounded_reader,
        args=(process.stderr, STDERR_DIAGNOSTICS_MAX_BYTES, stderr, stderr_overflow),
        name=f"provider-http-stderr-{process.pid}",
        daemon=True,
    )
    out_thread.start()
    err_thread.start()
    process.stdin.write(encoded)
    process.stdin.close()
    deadline = time.monotonic() + timeout - REAP_RESERVE_SECONDS
    timed_out = False
    try:
        while process.poll() is None:
            if stdout_overflow.is_set() or stderr_overflow.is_set():
                break
            if time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(0.01)
        if process.poll() is None:
            _reap(process)
        else:
            process.wait()
    except IsolatedHttpReapError:
        raise
    finally:
        out_thread.join(timeout=1.0)
        err_thread.join(timeout=1.0)
    if process.poll() is None:
        raise IsolatedHttpReapError(
            "Provider transport process could not be reaped", child_pid=process.pid
        )
    if timed_out:
        raise IsolatedHttpTimeout("Selected provider request timed out", child_pid=process.pid)
    if stdout_overflow.is_set() or stderr_overflow.is_set():
        raise IsolatedHttpResponseTooLarge(
            "Selected provider response exceeded its byte limit", child_pid=process.pid
        )
    try:
        result = jsonlib.loads(bytes(stdout).decode("utf-8"))
    except (UnicodeDecodeError, jsonlib.JSONDecodeError) as exc:
        raise IsolatedHttpProtocolError(
            "Provider transport returned an invalid response envelope", child_pid=process.pid
        ) from exc
    if not isinstance(result, dict):
        raise IsolatedHttpProtocolError("Provider transport returned an invalid response envelope", child_pid=process.pid)
    if result.get("error") == "provider_response_too_large":
        raise IsolatedHttpResponseTooLarge(
            "Selected provider response exceeded its byte limit", child_pid=process.pid
        )
    if result.get("error") == "provider_timeout":
        raise IsolatedHttpTimeout("Selected provider request timed out", child_pid=process.pid)
    if result.get("error"):
        raise IsolatedHttpError("Selected provider transport failed", child_pid=process.pid)
    body = result.get("body")
    status_code = result.get("status_code")
    if not isinstance(body, str) or not isinstance(status_code, int):
        raise IsolatedHttpProtocolError("Provider transport returned an invalid response envelope", child_pid=process.pid)
    return IsolatedHttpResponse(status_code=status_code, body=body.encode("utf-8"), child_pid=process.pid)


def isolated_post(url: str, **kwargs: Any) -> IsolatedHttpResponse:
    return isolated_request(url, method="POST", **kwargs)


def json_module_dumps(value: Any) -> bytes:
    return jsonlib.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _child_main() -> int:
    try:
        request = jsonlib.loads(sys.stdin.buffer.read(STDOUT_PROTOCOL_MAX_BYTES).decode("utf-8"))
        response = requests.request(
            str(request["method"]),
            str(request["url"]),
            headers=dict(request.get("headers") or {}),
            json=request.get("json"),
            timeout=float(request["timeout"]),
            stream=True,
        )
        limit = int(request["response_body_max_bytes"])
        body = bytearray()
        for chunk in response.iter_content(chunk_size=8192):
            if len(body) + len(chunk) > limit:
                sys.stdout.buffer.write(json_module_dumps({"error": "provider_response_too_large"}))
                return 0
            body.extend(chunk)
        try:
            decoded = bytes(body).decode("utf-8")
        except UnicodeDecodeError:
            sys.stdout.buffer.write(json_module_dumps({"error": "provider_protocol_invalid"}))
            return 0
        sys.stdout.buffer.write(json_module_dumps({"status_code": response.status_code, "body": decoded}))
        return 0
    except requests.Timeout:
        sys.stdout.buffer.write(json_module_dumps({"error": "provider_timeout"}))
        return 0
    except Exception as exc:
        sys.stdout.buffer.write(json_module_dumps({"error": "provider_transport_failed", "kind": type(exc).__name__}))
        return 0


if __name__ == "__main__":
    raise SystemExit(_child_main() if "--child" in sys.argv else 2)
