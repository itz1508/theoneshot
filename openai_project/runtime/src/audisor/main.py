"""FastAPI application entrypoint."""

from fastapi import FastAPI

from audisor.api.auth import router as auth_router
from audisor.api.builds import router as builds_router
from audisor.api.executions import canonical_router as canonical_executions_router, router as executions_router
from audisor.api.health import router as health_router
from audisor.api.tasks import canonical_router as canonical_tasks_router, router as tasks_router
from audisor.api.synonyms import router as synonyms_router


def create_app() -> FastAPI:
    """Create the local Audisor API application."""
    application = FastAPI(title="Audisor", version="0.9.0")
    application.include_router(health_router)
    application.include_router(auth_router)
    application.include_router(tasks_router)
    application.include_router(synonyms_router)
    application.include_router(builds_router)
    application.include_router(executions_router)
    application.include_router(canonical_tasks_router)
    application.include_router(canonical_executions_router)
    return application


app = create_app()
