"""Declared HTTP error contracts shared by FastAPI and schema probes."""

from __future__ import annotations

from typing import Any, Union

from pydantic import BaseModel, ConfigDict, RootModel


class ErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    code: str
    message: str


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    detail: ErrorDetail


class ValidationIssue(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str
    loc: list[Union[str, int]]
    msg: str
    input: Any


class ValidationErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detail: list[ValidationIssue]


class Declared422Response(RootModel[ErrorResponse | ValidationErrorResponse]):
    pass
