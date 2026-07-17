"""Deterministic renderer for generated one-time SKILL.md artifacts."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass

from audisor.builder.coverage import validate_task_coverage
from audisor.schemas.build import BuildTask, TaskSkill, validate_safe_identifier

MAX_SKILL_DIRECTORY_LENGTH = 120
SAFE_SKILL_DIRECTORY_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,118}[a-z0-9])?$")


class SkillRenderingError(ValueError):
    """A task could not be rendered to a safe deterministic skill."""


@dataclass(frozen=True)
class RenderedSkill:
    """One rendered skill and its safe relative directory."""

    task_id: str
    directory_name: str
    content: str

    def as_worker_task(self) -> TaskSkill:
        """Return the exact AMD-compatible task boundary."""
        return TaskSkill(task_id=self.task_id, prompt=self.content)


def slugify(value: str, max_length: int) -> str:
    """Create a deterministic lowercase ASCII path component."""
    ascii_value = (
        unicodedata.normalize("NFKD", value)
        .encode("ascii", "ignore")
        .decode("ascii")
        .casefold()
    )
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
    slug = slug[:max_length].rstrip("-")
    return slug or "task"


def render_skill(build_id: str, task: BuildTask) -> RenderedSkill:
    """Render one validated task with fixed frontmatter and LF endings."""
    validate_safe_identifier(build_id, "build_id")
    validate_task_coverage(task)

    task_slug = slugify(task.task_id, 64)
    remaining = MAX_SKILL_DIRECTORY_LENGTH - len(task_slug) - 1
    title_slug = slugify(task.title, max(1, min(48, remaining)))
    directory_name = f"{task_slug}-{title_slug}"
    if (
        len(directory_name) > MAX_SKILL_DIRECTORY_LENGTH
        or not SAFE_SKILL_DIRECTORY_RE.fullmatch(directory_name)
    ):
        raise SkillRenderingError("generated skill directory is unsafe")

    prompt = task.prompt.replace("\r\n", "\n").replace("\r", "\n").strip()
    validation = json.dumps(
        [command.model_dump(mode="json") for command in task.validation],
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    expected_outputs = json.dumps(
        task.expected_outputs,
        ensure_ascii=False,
        indent=2,
    )
    content = "\n".join(
        [
            "---",
            f"name: {directory_name}",
            f"description: One-time generated task for Audisor build {build_id}.",
            "---",
            "",
            f"# {task.title}",
            "",
            prompt,
            "",
            "## Trusted expected outputs",
            "```json",
            expected_outputs,
            "```",
            "",
            "## Deferred executable validation metadata",
            "```json",
            validation,
            "```",
            "",
        ]
    )
    rendered = RenderedSkill(
        task_id=task.task_id,
        directory_name=directory_name,
        content=content,
    )
    rendered.as_worker_task()
    return rendered


def render_skills(build_id: str, tasks: list[BuildTask]) -> list[RenderedSkill]:
    """Render skills in plan order and reject filesystem-equivalent collisions."""
    rendered: list[RenderedSkill] = []
    seen_directories: set[str] = set()
    for task in tasks:
        skill = render_skill(build_id, task)
        key = skill.directory_name.casefold()
        if key in seen_directories:
            raise SkillRenderingError("generated skill directories collide")
        seen_directories.add(key)
        rendered.append(skill)
    return rendered
