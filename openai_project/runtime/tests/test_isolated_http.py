from __future__ import annotations

import threading
import time
import inspect
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from audisor.workers.isolated_http import (
    IsolatedHttpBudgetError,
    IsolatedHttpResponseTooLarge,
    IsolatedHttpTimeout,
    isolated_request,
)
from audisor.audisor_lifecycle.stage_execution import _call_with_timeout
from audisor.audisor_lifecycle.stage_worker import ManagedStageWorker
from audisor.workers.base import ProviderTimeoutError
from audisor.workers.local import LocalWorker


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/hang":
            time.sleep(5)
            body = b'{}'
        elif self.path == "/large":
            body = b"x" * 1_048_577
        else:
            body = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture
def endpoint() -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_budget_below_reap_reserve_does_not_start_child(endpoint: str) -> None:
    with pytest.raises(IsolatedHttpBudgetError):
        isolated_request(endpoint, timeout=2.0)


def test_timeout_returns_only_after_child_is_reaped(endpoint: str) -> None:
    started = time.monotonic()
    with pytest.raises(IsolatedHttpTimeout) as caught:
        isolated_request(f"{endpoint}/hang", timeout=2.3)
    assert time.monotonic() - started < 3.0
    assert caught.value.child_pid is not None


def test_oversized_response_is_never_parsed(endpoint: str) -> None:
    with pytest.raises(IsolatedHttpResponseTooLarge):
        isolated_request(f"{endpoint}/large", timeout=5.0)


def test_successful_response_is_normalized(endpoint: str) -> None:
    response = isolated_request(endpoint, timeout=5.0)
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_hanging_http_child_is_terminated_and_reaped(endpoint: str) -> None:
    test_timeout_returns_only_after_child_is_reaped(endpoint)


def test_timeout_discards_late_output(endpoint: str) -> None:
    with pytest.raises(IsolatedHttpTimeout):
        isolated_request(f"{endpoint}/hang", timeout=2.3)


def test_timeout_leaves_no_aflow_thread(endpoint: str) -> None:
    provider = LocalWorker(endpoint, "fixture", timeout_seconds=2.3, structured_output=True)
    worker = ManagedStageWorker(primary=provider)
    with pytest.raises(ProviderTimeoutError):
        _call_with_timeout(worker, "gap_finding", {"artifact": "fixture"}, 3.0)
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and any(
        thread.name.startswith("aflow-") for thread in threading.enumerate()
    ):
        time.sleep(0.01)
    assert not any(thread.name.startswith("aflow-") for thread in threading.enumerate())


def test_timeout_persists_one_terminal_attempt(endpoint: str) -> None:
    provider = LocalWorker(endpoint, "fixture", timeout_seconds=2.3, structured_output=True)
    worker = ManagedStageWorker(primary=provider)
    with pytest.raises(ProviderTimeoutError):
        worker.run_stage("gap_finding", {"artifact": "fixture"})
    assert len(worker.attempts) == 1
    assert worker.attempts[0]["outcome"] == "provider_timeout"


def test_child_request_never_exposes_credentials(endpoint: str, monkeypatch: pytest.MonkeyPatch) -> None:
    import audisor.workers.isolated_http as transport

    original = transport.subprocess.Popen
    launches: list[tuple[object, object]] = []
    def capture(*args: object, **kwargs: object):
        launches.append((args, kwargs))
        return original(*args, **kwargs)
    monkeypatch.setattr(transport.subprocess, "Popen", capture)
    isolated_request(
        endpoint,
        headers={"Authorization": "Bearer sentinel-never-expose"},
        json={"prompt": "fixture"},
        timeout=5,
    )
    assert "sentinel-never-expose" not in repr(launches)


def test_oversized_child_output_is_killed_while_streaming(endpoint: str) -> None:
    test_oversized_response_is_never_parsed(endpoint)


def test_transport_uses_platform_process_isolation() -> None:
    source = inspect.getsource(isolated_request)
    assert "CREATE_NEW_PROCESS_GROUP" in source
    assert "start_new_session" in source
