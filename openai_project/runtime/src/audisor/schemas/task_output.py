"""Audisor task output schema."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr


class TaskOutput(BaseModel):
    """One normalized task result."""

    model_config = ConfigDict(extra="forbid")

    task_id: Annotated[str, Field(strict=True)]
    answer: Annotated[str, Field(strict=True)]
    _http_status: int | None = PrivateAttr(default=None)
    _transport_succeeded: bool | None = PrivateAttr(default=None)
    _finish_reason: str | None = PrivateAttr(default=None)
    _tool_call_present: bool | None = PrivateAttr(default=None)
    _choice_count: int | None = PrivateAttr(default=None)

    @property
    def http_status(self) -> int | None:
        return self._http_status

    @property
    def transport_succeeded(self) -> bool | None:
        return self._transport_succeeded

    @property
    def finish_reason(self) -> str | None:
        return self._finish_reason

    @property
    def tool_call_present(self) -> bool | None:
        return self._tool_call_present

    @property
    def choice_count(self) -> int | None:
        return self._choice_count

    def set_response_metadata(self, *, http_status: int | None, transport_succeeded: bool | None, finish_reason: str | None, tool_call_present: bool | None, choice_count: int | None) -> "TaskOutput":
        self._http_status = http_status
        self._transport_succeeded = transport_succeeded
        self._finish_reason = finish_reason
        self._tool_call_present = tool_call_present
        self._choice_count = choice_count
        return self

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TaskOutput):
            return NotImplemented
        return self.task_id == other.task_id and self.answer == other.answer
