"""FastAPI application entrypoint."""

from fastapi import FastAPI

from audisor.api.builds import router as builds_router
from audisor.api.executions import router as executions_router
from audisor.api.health import router as health_router
from audisor.api.tasks import router as tasks_router


def create_app() -> FastAPI:
    """Create the local Audisor API application."""
    application = FastAPI(title="Audisor", version="0.1.0")
    application.include_router(health_router)
    application.include_router(tasks_router)
    application.include_router(builds_router)
    application.include_router(executions_router)
    return application


app = create_app()
