"""Application factory and development entrypoint."""
from __future__ import annotations

import os
import re

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import chat_router, router
from .api.operations_routes import operations_router
from .api.aflow_routes import aflow_router
from .api.usage_routes import usage_router
from .application.fix_engines import build_grammar_state
from .application.operator_chat_prompt import OperatorChatClock, default_clock
from .application.service import AssistantService
from audisor.audisor_lifecycle.management import initialize_management_state

CORS_ORIGINS_VAR = "AUDISOR_CORS_ORIGINS"
CLOUD_API_KEY_VAR = "AUDISOR_ASSISTANT_CLOUD_API_KEY"

#: Provider ids whose credential must exist before the app may start.
_CLOUD_PROVIDER_IDS = {"cloud-openai-compatible", "cloud-anthropic"}

# Exactly scheme://host[:port] — no path, query, fragment, or userinfo.
_ORIGIN_PATTERN = re.compile(
    r"^(?P<scheme>https?)://(?P<host>[a-z0-9]([a-z0-9.-]*[a-z0-9])?)(?::(?P<port>\d{1,5}))?$",
    re.IGNORECASE,
)


def parse_cors_origins(raw: str | None) -> list[str]:
    """Parse AUDISOR_CORS_ORIGINS into an exact allow-list.

    Comma-separated entries are trimmed, deduplicated (order-preserving),
    and lowercased on scheme+host so they match the browser's Origin header
    byte-for-byte.  Wildcards and anything that is not a bare
    ``http(s)://host[:port]`` origin fail fast at startup — a malformed
    allow-list must never be partially applied.
    """
    if raw is None:
        return []
    origins: list[str] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "*" in entry:
            raise RuntimeError(
                f"{CORS_ORIGINS_VAR}: wildcard origins are not permitted: {entry!r}"
            )
        match = _ORIGIN_PATTERN.match(entry)
        if match is None:
            raise RuntimeError(
                f"{CORS_ORIGINS_VAR}: malformed origin (expected "
                f"http(s)://host[:port]): {entry!r}"
            )
        scheme = match.group("scheme").lower()
        host = match.group("host").lower()
        port = match.group("port")
        normalized = f"{scheme}://{host}" + (f":{port}" if port else "")
        if normalized not in origins:
            origins.append(normalized)
    return origins


def enforce_cloud_credential() -> None:
    """Fail fast when a cloud provider is selected without a credential.

    Mirrors the ``parse_cors_origins`` startup contract: a misconfigured
    deployment must never come up half-working.  Local and fake providers
    are unaffected.
    """
    selected = os.environ.get("AUDISOR_PROVIDER", "local-openai-compatible").strip()
    if selected in _CLOUD_PROVIDER_IDS and not os.environ.get(
        CLOUD_API_KEY_VAR, ""
    ).strip():
        raise RuntimeError(
            f"AUDISOR_PROVIDER={selected!r} requires {CLOUD_API_KEY_VAR} to be set."
        )


def create_app(
    service: AssistantService | None = None,
    *,
    operator_chat_clock: OperatorChatClock | None = None,
) -> FastAPI:
    """Build the FastAPI app.  A pre-built service (e.g. with the
    deterministic fake provider) may be injected for tests.

    ``operator_chat_clock`` overrides the wall-clock used to build the
    operator-chat system prompt.  Tests pass a fixed clock here;
    production leaves it ``None`` (uses the default clock).
    """
    if service is None:
        # Injected services bypass provider construction, so the cloud
        # credential check only applies to environment-driven startup.
        enforce_cloud_credential()
    app = FastAPI(title="Audisor Writing & Design Assistant", version="0.1.0")
    app.state.service = service
    app.state.operator_chat_clock = operator_chat_clock or default_clock
    # Grammar engine resolves eagerly at startup so fix_wording requests
    # never trigger a surprise download; failure never blocks startup.
    app.state.grammar_state = build_grammar_state()
    # Additive management initialization imports legacy error results without
    # modifying the lifecycle artifacts themselves.
    initialize_management_state()
    # Opt-in strict CORS: default (unset/empty) adds no middleware at all,
    # preserving same-origin and Vite-proxy behaviour exactly.
    cors_origins = parse_cors_origins(os.environ.get(CORS_ORIGINS_VAR))
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["content-type", "x-audisor-dev-user"],
            allow_credentials=False,
        )
    app.include_router(router)
    app.include_router(chat_router)
    app.include_router(operations_router)
    app.include_router(aflow_router)
    app.include_router(usage_router)
    return app


def main() -> None:  # pragma: no cover - development runner
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=8799)


if __name__ == "__main__":  # pragma: no cover
    main()
