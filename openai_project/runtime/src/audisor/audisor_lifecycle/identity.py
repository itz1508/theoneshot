"""Runtime-derived submission identity: canonicalisation and digests."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


def _canonical_content(text: str) -> str:
    """Documented canonical representation used for digesting only.

    Canonicalisation is line-ending normalization alone: CRLF and CR become
    LF. Nothing else is altered — trailing whitespace and blank lines stay
    significant, so the digest is safe for patches, source code, YAML,
    whitespace-sensitive templates, and Markdown hard breaks, not just
    prose. The original submitted text is always retained verbatim in the
    result.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _content_digest(content: str) -> str:
    """Canonical content digest: drives the runtime-derived revision sequence."""
    return hashlib.sha256(_canonical_content(content).encode("utf-8")).hexdigest()


def _submission_digest(content: str, trigger: Mapping[str, Any]) -> str:
    """Runtime-derived identity of one submission.

    Covers everything the lifecycle reasons over — canonical content, intent,
    context, artifact_type — so resubmitting the same content with new
    context (the unresolved-gap recovery path) is a new run, never a replay.
    """
    material = json.dumps(
        {
            "content": _canonical_content(content),
            "artifact_type": trigger.get("artifact_type"),
            "intent": trigger.get("intent"),
            "context": trigger.get("context"),
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
