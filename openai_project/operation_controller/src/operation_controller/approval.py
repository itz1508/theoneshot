"""Approval authority — immutable argument binding for tool approval.

Computes a digest that binds an approval decision to the exact tool call
(call_id + tool_name + arguments). Any alteration of the approved command
invalidates the digest, preventing argument substitution attacks.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def compute_argument_digest(call_id: str, tool_name: str, arguments: dict[str, Any]) -> str:
    """Compute a SHA-256 digest binding approval to exact tool call arguments.

    The digest covers:
    - call_id: the unique tool call identifier from the model
    - tool_name: the tool being approved
    - arguments: the exact arguments (canonical JSON, sorted keys)

    Any alteration of these fields invalidates the digest.
    """
    canonical = json.dumps(
        {"call_id": call_id, "tool_name": tool_name, "arguments": arguments},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_argument_digest(
    call_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    expected_digest: str,
) -> bool:
    """Verify that the arguments match the expected digest.

    Returns True if the digest matches, False otherwise.
    """
    actual = compute_argument_digest(call_id, tool_name, arguments)
    return actual == expected_digest
