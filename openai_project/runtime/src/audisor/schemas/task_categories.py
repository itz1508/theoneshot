"""Audisor task-category schema."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TaskCategory(StrEnum):
    """Stable Audisor category identifiers."""

    A1 = "a1"
    A2 = "a2"
    A3 = "a3"
    A4 = "a4"
    A5 = "a5"
    A6 = "a6"
    A7 = "a7"
    A8 = "a8"


def parse_category_id(value: str) -> TaskCategory:
    """Parse one of the eight supported Audisor category identifiers."""

    try:
        return TaskCategory(value)
    except ValueError as exc:
        raise ValueError(f"Unsupported Audisor category: {value!r}") from exc


@dataclass(frozen=True, slots=True)
class CategoryDefinition:
    """Audisor-owned meaning for one category identifier."""

    category_id: TaskCategory
    name: str
    description: str


CATEGORY_DEFINITIONS: tuple[CategoryDefinition, ...] = (
    CategoryDefinition(TaskCategory.A1, "factual knowledge", "Explain facts, concepts, and how things work."),
    CategoryDefinition(TaskCategory.A2, "mathematical reasoning", "Solve arithmetic, percentages, and quantitative problems."),
    CategoryDefinition(TaskCategory.A3, "sentiment classification", "Classify sentiment and explain the classification."),
    CategoryDefinition(TaskCategory.A4, "text summarisation", "Condense text under a stated meaning or length constraint."),
    CategoryDefinition(TaskCategory.A5, "named entity recognition", "Identify and label people, organisations, places, and dates."),
    CategoryDefinition(TaskCategory.A6, "code debugging", "Identify defects and provide a corrected implementation."),
    CategoryDefinition(TaskCategory.A7, "logical reasoning", "Resolve constraints and deductive conditions."),
    CategoryDefinition(TaskCategory.A8, "code generation", "Write a correct implementation from a specification."),
)


CATEGORY_BY_ID: dict[TaskCategory, CategoryDefinition] = {
    definition.category_id: definition for definition in CATEGORY_DEFINITIONS
}
