"""Structural coverage validation for generated one-time task prompts."""

from __future__ import annotations

import re

from audisor.schemas.build import BuildTask

REQUIRED_SECTIONS = (
    "Objective",
    "Inputs and repository paths",
    "Required work",
    "Ordered steps",
    "Expected output",
    "Validation",
    "Evidence to return",
)
ENUMERATED_STEP_RE = re.compile(r"(?m)^\s*\d+[.)]\s+\S")
PLACEHOLDER_TEXT = {
    "fill me in",
    "insert content",
    "n a",
    "none",
    "not specified",
    "placeholder",
    "tbd",
    "todo",
    "unknown",
}


class CoverageValidationError(ValueError):
    """A task prompt lacks executable structural coverage."""


def _placeholder_normal_form(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _is_placeholder(value: str) -> bool:
    stripped = value.strip()
    if stripped in {"...", "…"}:
        return True
    if (
        (stripped.startswith("<") and stripped.endswith(">"))
        or (stripped.startswith("{{") and stripped.endswith("}}"))
        or stripped.casefold().startswith("[insert ")
    ):
        return True
    return _placeholder_normal_form(stripped) in PLACEHOLDER_TEXT


def validate_task_coverage(task: BuildTask) -> None:
    """Require one non-empty, non-placeholder occurrence of every section."""
    if _is_placeholder(task.title):
        raise CoverageValidationError(f"task {task.task_id} has a placeholder title")

    matches: list[tuple[str, re.Match[str]]] = []
    for section in REQUIRED_SECTIONS:
        pattern = re.compile(
            rf"(?im)^#{{1,6}}[ \t]+{re.escape(section)}[ \t]*$"
        )
        section_matches = list(pattern.finditer(task.prompt))
        if len(section_matches) != 1:
            raise CoverageValidationError(
                f"task {task.task_id} must contain exactly one {section} section"
            )
        matches.append((section, section_matches[0]))

    positions = [match.start() for _, match in matches]
    if positions != sorted(positions):
        raise CoverageValidationError(
            f"task {task.task_id} sections are not in the required order"
        )

    for index, (section, match) in enumerate(matches):
        end = matches[index + 1][1].start() if index + 1 < len(matches) else len(task.prompt)
        content = task.prompt[match.end() : end].strip()
        if not content or _is_placeholder(content):
            raise CoverageValidationError(
                f"task {task.task_id} has an incomplete {section} section"
            )
        if section == "Ordered steps" and not ENUMERATED_STEP_RE.search(content):
            raise CoverageValidationError(
                f"task {task.task_id} must contain an enumerated ordered step"
            )


def validate_plan_coverage(tasks: list[BuildTask]) -> None:
    """Validate every task before any artifact is rendered or written."""
    for task in tasks:
        validate_task_coverage(task)
