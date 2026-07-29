"""Documented request limits.

These bounds are the public contract for request size.  The request schema
enforces them; the web client mirrors them for early feedback.
"""
from __future__ import annotations

#: Maximum characters accepted in ``text``.
MAX_TEXT_CHARS = 20_000

#: Maximum characters accepted in ``selected_text``.
MAX_SELECTED_TEXT_CHARS = 1_000

#: Maximum characters accepted in ``context``.
MAX_CONTEXT_CHARS = 8_000

#: Maximum characters accepted in ``tone``.
MAX_TONE_CHARS = 200

#: Maximum characters accepted in ``request_id`` and ``workspace_id``.
MAX_ID_CHARS = 128

#: Maximum characters accepted in the optional ``model`` override.
MAX_MODEL_CHARS = 200

#: Allowed shape of a model identifier (name, tag, or path segments only).
MODEL_NAME_PATTERN = r"^[A-Za-z0-9._:/-]+$"
