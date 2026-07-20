from audisor.schemas.task_categories import (
    CATEGORY_BY_ID,
    CATEGORY_DEFINITIONS,
    TaskCategory,
    parse_category_id,
)


def test_a1_to_a8_define_the_eight_categories_in_order():
    assert [definition.category_id for definition in CATEGORY_DEFINITIONS] == list(TaskCategory)
    assert list(CATEGORY_BY_ID) == list(TaskCategory)
    assert [definition.name for definition in CATEGORY_DEFINITIONS] == [
        "factual knowledge",
        "mathematical reasoning",
        "sentiment classification",
        "text summarisation",
        "named entity recognition",
        "code debugging",
        "logical reasoning",
        "code generation",
    ]


def test_category_definition_is_audisor_owned_and_not_task_id_inferred():
    assert CATEGORY_BY_ID[TaskCategory.A1].description.startswith("Explain facts")
    assert not hasattr(CATEGORY_BY_ID[TaskCategory.A1], "task_id")


def test_unknown_category_ids_are_rejected():
    assert parse_category_id("a1") is TaskCategory.A1
    try:
        parse_category_id("a9")
    except ValueError as exc:
        assert str(exc) == "Unsupported Audisor category: 'a9'"
    else:
        raise AssertionError("unknown category ID was accepted")
