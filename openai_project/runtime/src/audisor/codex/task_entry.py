from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path
from typing import Any, Callable

from dulwich import porcelain
from dulwich.repo import Repo

from audisor.builder.preparer import BuildPreparer
from audisor.builder.store import BuildStore
from audisor.routing.configuration import get_provider_router
from audisor.schemas.build import BuildExecutionContext, BuildRequest


def _repository_context(target: Path, build_id: str, allowed_write_paths: list[str]) -> BuildExecutionContext:
    repository = Repo.discover(target)
    root = Path(repository.path).resolve()
    try:
        head_value = repository.head()
    except KeyError:
        head_value = None
    head = head_value.decode("ascii") if head_value else "unborn"
    repository.close()
    status = porcelain.status(Repo(str(root)), ignored=False, untracked_files="all")
    dirty = bool(status.staged or status.unstaged or status.untracked)
    return BuildExecutionContext.seal(
        target_root=str(target.resolve()),
        repository_identity={
            "root_reference": str(root),
            "revision": head,
            "dirty_state": "dirty" if dirty else "clean",
        },
        allowed_write_paths=allowed_write_paths,
        authority_limits={
            "mutation_authorized": False,
            "execution_authorized": False,
            "apply_authorized": False,
            "completion_claimed": False,
        },
        workspace_identity={"workspace_id": build_id, "root_reference": str(root)},
        success_definition={"required": ["prepared plan validates", "host continuation is permitted"]},
        validation_requirements=[{"kind": "prepared-plan-validation", "required": True}],
    )


def prepare_and_run_task(
    task: str,
    *,
    target_root: str | Path | None = None,
    allowed_write_paths: list[str] | None = None,
    preparer_factory: Callable[[BuildStore], BuildPreparer] | None = None,
    adapter_factory: Callable[[], Any] | None = None,
) -> Any:
    if not task or not task.strip():
        raise ValueError("build_task_required")
    target = Path(target_root or Path.cwd()).expanduser().resolve()
    if not target.is_dir():
        raise ValueError("build_target_unavailable")
    selected_paths = allowed_write_paths or [
        name for name in ("src", "tests") if (target / name).is_dir()
    ]
    configured = os.environ.get("AUDISOR_ALLOWED_WRITE_PATHS", "").strip()
    if configured:
        selected_paths = [item.strip() for item in configured.split(",") if item.strip()]
    if not selected_paths:
        raise ValueError("build_authority_scope_required")
    seed = f"{task}\0{uuid.uuid4().hex}".encode("utf-8")
    build_id = f"build-{hashlib.sha256(seed).hexdigest()[:20]}"
    request = BuildRequest(
        build_id=build_id,
        instruction=task.strip(),
        execution_context=_repository_context(target, build_id, selected_paths),
    )
    store = BuildStore.from_environment()
    preparer = preparer_factory(store) if preparer_factory is not None else BuildPreparer(get_provider_router(), store)
    preparer.prepare(request)
    adapter = adapter_factory() if adapter_factory is not None else None
    if adapter is None:
        from .adapter import CodexAdapter

        adapter = CodexAdapter()
    return adapter.run(build_id)
