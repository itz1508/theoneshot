"""AMD-compatible task input schemas."""

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator, model_validator


class TaskInput(BaseModel):
    """One task. Whitespace is validated but original strings are preserved."""

    model_config = ConfigDict(extra="ignore")

    task_id: Annotated[str, Field(strict=True)]
    prompt: Annotated[str, Field(strict=True)]

    @field_validator("task_id", "prompt")
    @classmethod
    def require_non_empty_string(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must be a non-empty string")
        return value


class TaskInputBatch(RootModel[list[TaskInput]]):
    """Non-empty task batch with exact, raw task ID uniqueness."""

    @model_validator(mode="after")
    def validate_batch(self) -> Self:
        if not self.root:
            raise ValueError("request body must contain at least one task")

        seen: set[str] = set()
        duplicates: list[str] = []
        for task in self.root:
            if task.task_id in seen and task.task_id not in duplicates:
                duplicates.append(task.task_id)
            seen.add(task.task_id)
        if duplicates:
            raise ValueError(f"duplicate task_id values: {duplicates}")
        return self
