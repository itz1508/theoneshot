"""AMD-compatible task output schema."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class TaskOutput(BaseModel):
    """One normalized task result."""

    model_config = ConfigDict(extra="forbid")

    task_id: Annotated[str, Field(strict=True)]
    answer: Annotated[str, Field(strict=True)]
