"""Privacy policy: sanitized metadata, safe errors, and diagram sanitizing.

Raw user text is never logged by default.  Only the sanitized metadata
record defined here may be persisted or logged by the backend.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Mapping

#: Substrings that must never appear in public error messages.
_SENSITIVE_PATTERNS = (
    re.compile(r"(?i)bearer\s+\S+"),
    re.compile(r"(?i)api[_-]?key\S*"),
    re.compile(r"(?i)authorization"),
    re.compile(r"[A-Za-z]:\\[^\s\"']+"),  # windows filesystem paths
    re.compile(r"/(?:home|usr|var|etc)/[^\s\"']+"),  # unix filesystem paths
)

#: Mermaid directives that can execute or link out; stripped before return.
_DIAGRAM_BLOCKLIST = (
    re.compile(r"(?im)^\s*click\s.*$"),
    re.compile(r"(?i)javascript:"),
    re.compile(r"(?is)<script.*?>.*?</script>"),
    re.compile(r"(?i)<script[^>]*>"),
    re.compile(r"(?im)^\s*%%\{.*\}%%\s*$"),  # init/config directives
)


def sanitize_public_message(message: str) -> str:
    """Strip credential-like tokens and filesystem paths from a message."""
    cleaned = message
    for pattern in _SENSITIVE_PATTERNS:
        cleaned = pattern.sub("[redacted]", cleaned)
    return cleaned


def sanitize_diagram_code(code: str) -> tuple[str, list[str]]:
    """Remove executable or external-loading Mermaid constructs.

    Returns the sanitized code and warnings describing what was removed.
    """
    warnings: list[str] = []
    cleaned = code
    for pattern in _DIAGRAM_BLOCKLIST:
        if pattern.search(cleaned):
            cleaned = pattern.sub("", cleaned)
            warnings.append("Removed a disallowed diagram directive.")
    return cleaned.strip(), warnings


def sanitized_request_record(
    *,
    request_id: str,
    mode: str,
    status: str,
    provider_source: str | None,
    started_at: datetime,
    usage: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the only request record the backend stores or logs by default.

    Deliberately excludes text, selected_text, context, tone, results,
    prompts, and any provider payload.
    """
    return {
        "request_id": request_id,
        "mode": mode,
        "status": status,
        "provider_source": provider_source,
        "started_at": started_at.astimezone(timezone.utc).isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "usage": dict(usage) if usage else None,
    }
