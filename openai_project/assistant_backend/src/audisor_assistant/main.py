"""Application factory and development entrypoint."""
from __future__ import annotations

import os
import re

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import router
from .application.fix_engines import build_grammar_state
from .application.service import AssistantService

CORS_ORIGINS_VAR = "AUDISOR_CORS_ORIGINS"

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


def create_app(service: AssistantService | None = None) -> FastAPI:
    """Build the FastAPI app.  A pre-built service (e.g. with the
    deterministic fake provider) may be injected for tests."""
    app = FastAPI(title="Audisor Writing & Design Assistant", version="0.1.0")
    app.state.service = service
    # Grammar engine resolves eagerly at startup so fix_wording requests
    # never trigger a surprise download; failure never blocks startup.
    app.state.grammar_state = build_grammar_state()
    # Opt-in strict CORS: default (unset/empty) adds no middleware at all,
    # preserving same-origin and Vite-proxy behaviour exactly.
    cors_origins = parse_cors_origins(os.environ.get(CORS_ORIGINS_VAR))
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_methods=["POST", "OPTIONS"],
            allow_headers=["content-type", "x-audisor-dev-user"],
            allow_credentials=False,
        )
    app.include_router(router)
    return app


def main() -> None:  # pragma: no cover - development runner
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=8799)


if __name__ == "__main__":  # pragma: no cover
    main()
