"""Runtime sentinel tests for the 0.10.0 tombstone release.

Proves that deprecated HTTP routes and CLI commands never construct
legacy runtime objects, make model requests, or write artifacts.
Sentinels are monkeypatched to raise immediately if called.

Test list (maps to user requirements 1-8):
  1. Every deprecated route returns 410.
  2. /health returns 200 with tombstone status.
  3. /ready returns 200 with legacy_runtime_available=false.
  4. Deprecated routes do not execute dependency providers.
  5. Deprecated CLI commands remain recognized and exit 1.
  6. Deprecated CLI commands do not read provider configuration.
  7. No model request occurs.
  8. No Build execution artifact is created.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from audisor.cli import main as cli_main
from audisor.main import create_app


# ---------------------------------------------------------------------------
# Sentinel helper
# ---------------------------------------------------------------------------

class SentinelCalled(AssertionError):
    """Raised when a patched legacy constructor or method is invoked."""


def _sentinel_raiser(*args, **kwargs):
    raise SentinelCalled("legacy runtime object was constructed or invoked")


@pytest.fixture
def sentinels(monkeypatch, tmp_path):
    """Patch every legacy constructor / method to raise immediately."""
    monkeypatch.setenv("AUDISOR_DATA_DIR", str(tmp_path / "data"))

    # Clear all lru_cache caches so sentinels cannot be bypassed by stale cache.
    from audisor.routing.configuration import get_provider_router
    get_provider_router.cache_clear()

    from audisor.api.tasks import get_task_service, get_canonical_operation_service as _tcs
    get_task_service.cache_clear()
    _tcs.cache_clear()

    from audisor.api.builds import get_build_preparer
    get_build_preparer.cache_clear()

    from audisor.api.executions import get_build_executor, get_canonical_operation_service as _ecs
    get_build_executor.cache_clear()
    _ecs.cache_clear()

    from audisor.api.synonyms import get_synonym_service
    get_synonym_service.cache_clear()

    from audisor.api.auth import get_auth_service
    get_auth_service.cache_clear()

    # Patch get_provider_router on the module so lazy imports get the sentinel
    import audisor.routing.configuration as routing_config
    monkeypatch.setattr(routing_config, "get_provider_router", _sentinel_raiser)

    # Patch class methods / constructors
    from audisor.routing.router import ProviderRouter
    monkeypatch.setattr(ProviderRouter, "select_provider", _sentinel_raiser)

    from audisor.workers.local import LocalWorker
    monkeypatch.setattr(LocalWorker, "execute", _sentinel_raiser)

    from audisor.builder.executor import BuildExecutor
    monkeypatch.setattr(BuildExecutor, "__init__", _sentinel_raiser)

    from audisor.builder.preparer import BuildPreparer
    monkeypatch.setattr(BuildPreparer, "__init__", _sentinel_raiser)

    from audisor.builder.store import BuildStore
    monkeypatch.setattr(BuildStore, "publish", _sentinel_raiser)

    from audisor.builder.execution_store import ExecutionStore
    monkeypatch.setattr(ExecutionStore, "claim", _sentinel_raiser)
    monkeypatch.setattr(ExecutionStore, "persist_state", _sentinel_raiser)
    monkeypatch.setattr(ExecutionStore, "persist_audisor_result", _sentinel_raiser)

    # Patch operation service constructors
    from audisor.operations.service import CanonicalOperationService
    monkeypatch.setattr(CanonicalOperationService, "__init__", _sentinel_raiser)
    try:
        from audisor.operations.service import AcceptedOperationService
        monkeypatch.setattr(AcceptedOperationService, "__init__", _sentinel_raiser)
    except ImportError:
        pass

    # Patch requests.post to catch any model HTTP dispatch
    import requests
    monkeypatch.setattr(requests, "post", _sentinel_raiser)

    return tmp_path


@pytest.fixture
def client(sentinels):
    return TestClient(create_app())


# ---------------------------------------------------------------------------
# 1. Every deprecated route returns 410
# ---------------------------------------------------------------------------

_DEPRECATED_ROUTES = [
    ("POST", "/v1/auth/register", {"username": "x", "password": "y"}),
    ("POST", "/v1/auth/login", {"username": "x", "password": "y"}),
    ("POST", "/v1/auth/logout", None),
    ("GET", "/v1/auth/me", None),
    ("POST", "/v1/tasks", [{"task_id": "t1", "prompt": "test"}]),
    ("POST", "/v1/synonyms", [{"task_id": "t1", "prompt": "test"}]),
    ("POST", "/v1/builds/prepare", {"build_id": "b1", "instruction": "test"}),
    ("POST", "/v1/builds/b1/executions", {
        "execution_id": "e1",
        "idempotency_key": "k1",
        "target_root": ".",
        "allowed_write_paths": ["src"],
    }),
    ("POST", "/v1/operations", {
        "operation_id": "op1",
        "operation_kind": "build",
        "client": {"client_id": "c", "adapter_id": "cli", "adapter_version": "1"},
        "repository": {"root_reference": "repo"},
        "requested_scope": {"paths": ["src"]},
        "build": {
            "build_id": "b1",
            "request": {
                "execution_id": "op1",
                "idempotency_key": "op1",
                "target_root": "src",
                "allowed_write_paths": ["src"],
            },
        },
    }),
    ("POST", "/v1/operations/tasks", [{"task_id": "t1", "prompt": "test"}]),
]


@pytest.mark.parametrize("method,path,body", _DEPRECATED_ROUTES)
def test_deprecated_route_returns_410(client, method, path, body):
    if body is not None:
        response = client.request(method, path, json=body)
    else:
        response = client.request(method, path)
    assert response.status_code == 410
    data = response.json()
    assert data["code"] == "legacy_runtime_deprecated"
    assert data["message"] == "The Audisor model-execution runtime is deprecated."
    assert data["removal_version"] == "1.0.0"


# ---------------------------------------------------------------------------
# 2. /health returns 200 with tombstone status
# ---------------------------------------------------------------------------

def test_health_returns_200_with_tombstone_status(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "deprecated",
        "serving_mode": "tombstone",
        "removal_version": "1.0.0",
    }


# ---------------------------------------------------------------------------
# 3. /ready returns 200 with legacy_runtime_available=false
# ---------------------------------------------------------------------------

def test_ready_returns_200_with_legacy_runtime_unavailable(client):
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {
        "ready": True,
        "serving_mode": "tombstone",
        "legacy_runtime_available": False,
    }


# ---------------------------------------------------------------------------
# 4. Deprecated routes do not execute dependency providers
#    (sentinels are patched — if any is called, SentinelCalled is raised)
# ---------------------------------------------------------------------------

def test_deprecated_routes_do_not_trigger_sentinels(client):
    """Exercising every tombstoned route must not trigger any sentinel."""
    for method, path, body in _DEPRECATED_ROUTES:
        if body is not None:
            client.request(method, path, json=body)
        else:
            client.request(method, path)
    # If we reach here, no sentinel was called.


# ---------------------------------------------------------------------------
# 5. Deprecated CLI commands remain recognized and exit 1
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("argv", [
    ["setup"],
    ["host"],
    ["host", "accept"],
    ["host", "accept", "--request-file", "/nonexistent.json"],
    ["codex"],
    ["codex", "--build-id", "test-build"],
    ["codex", "--build", "test.json"],
    ["run"],
    ["run", "--input", "/in.json", "--output", "/out.json"],
])
def test_deprecated_cli_command_exits_1(argv, sentinels, monkeypatch):
    monkeypatch.setenv("AUDISOR_CONFIG_PATH", str(sentinels / "config.json"))
    stderr = io.StringIO()
    exit_code = cli_main(argv, stdin=io.StringIO(), stdout=io.StringIO(), stderr=stderr)
    assert exit_code == 1
    assert "deprecated" in stderr.getvalue().lower()


# ---------------------------------------------------------------------------
# 6. Deprecated CLI commands do not read provider configuration
#    (get_provider_router sentinel is patched — would raise if called)
# ---------------------------------------------------------------------------

def test_deprecated_cli_commands_do_not_construct_providers(sentinels, monkeypatch):
    monkeypatch.setenv("AUDISOR_CONFIG_PATH", str(sentinels / "config.json"))
    for argv in (["setup"], ["host", "accept"], ["codex"], ["run"]):
        stderr = io.StringIO()
        cli_main(argv, stdin=io.StringIO(), stdout=io.StringIO(), stderr=stderr)
    # If we reach here, no sentinel was called.


# ---------------------------------------------------------------------------
# 7. No model request occurs (requests.post sentinel is patched)
#    (covered by test_deprecated_routes_do_not_trigger_sentinels above)
# ---------------------------------------------------------------------------

def test_no_model_request_occurs(client):
    """Exercising every tombstoned route must not call requests.post."""
    for method, path, body in _DEPRECATED_ROUTES:
        if body is not None:
            client.request(method, path, json=body)
        else:
            client.request(method, path)
    # If we reach here, requests.post was never called.


# ---------------------------------------------------------------------------
# 8. No Build execution artifact is created
# ---------------------------------------------------------------------------

def test_no_build_artifact_created(client, sentinels):
    data_dir = sentinels / "data"
    for method, path, body in _DEPRECATED_ROUTES:
        if body is not None:
            client.request(method, path, json=body)
        else:
            client.request(method, path)
    # Verify no build or execution directories were created
    if data_dir.exists():
        builds = list(data_dir.rglob("plan.json"))
        executions = list(data_dir.rglob("aflow-operation-result.json"))
        assert builds == [], f"Build artifacts created: {builds}"
        assert executions == [], f"Execution artifacts created: {executions}"


# ---------------------------------------------------------------------------
# /docs and /openapi.json remain available
# ---------------------------------------------------------------------------

def test_docs_available(client):
    response = client.get("/docs")
    assert response.status_code == 200


def test_openapi_available(client):
    response = client.get("/openapi.json")
    assert response.status_code == 200
    document = response.json()
    assert document["info"]["version"] == "0.10.0"


def test_deprecated_routes_marked_in_openapi(client):
    document = client.get("/openapi.json").json()
    deprecated_paths = []
    for path, methods in document.get("paths", {}).items():
        for method, info in methods.items():
            if info.get("deprecated"):
                deprecated_paths.append(f"{method.upper()} {path}")
    # At least the 10 functional legacy routes should be marked deprecated
    assert len(deprecated_paths) >= 10, (
        f"Expected >= 10 deprecated routes in OpenAPI, got {len(deprecated_paths)}: "
        f"{deprecated_paths}"
    )


# ---------------------------------------------------------------------------
# 9. audisor aflow behavior is unchanged
# ---------------------------------------------------------------------------

def test_aflow_status_works(sentinels, monkeypatch, capsys):
    monkeypatch.setenv("AUDISOR_CONFIG_PATH", str(sentinels / "config.json"))
    cli_main(["aflow", "off"], stderr=io.StringIO())
    capsys.readouterr()
    exit_code = cli_main(["aflow", "status"], stderr=io.StringIO())
    assert exit_code == 0
    assert "A-Flow: OFF" in capsys.readouterr().out


def test_aflow_toggle_works(sentinels, monkeypatch, capsys):
    monkeypatch.setenv("AUDISOR_CONFIG_PATH", str(sentinels / "config.json"))
    assert cli_main(["aflow", "on"], stderr=io.StringIO()) == 0
    capsys.readouterr()
    cli_main(["aflow", "status"], stderr=io.StringIO())
    assert "A-Flow: ON" in capsys.readouterr().out
    assert cli_main(["aflow", "off"], stderr=io.StringIO()) == 0
