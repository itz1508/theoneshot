"""Unicode-safe recursive evidence normalization."""

from __future__ import annotations

import json
import math

from audisor.builder.safe_json import json_safe_bytes, normalize_json_safe


def test_lone_surrogates_are_preserved_as_visible_safe_text_recursively() -> None:
    payload = {"worker": ["before\ud800after", {"error\udfff": "value\udc00"}]}

    content = json_safe_bytes(payload)
    decoded = content.decode("utf-8", errors="strict")
    loaded = json.loads(decoded)

    assert loaded["worker"][0] == "before\\ud800after"
    assert loaded["worker"][1]["error\\udfff"] == "value\\udc00"
    assert "\ud800" not in decoded


def test_exceptions_invalid_bytes_and_nonfinite_numbers_remain_serializable() -> None:
    payload = {
        "exception": RuntimeError("provider\ud800failure"),
        "bytes": b"valid\xffbytes",
        "nan": math.nan,
        "positive": math.inf,
        "negative": -math.inf,
    }

    loaded = json.loads(json_safe_bytes(payload))

    assert loaded["exception"] == {
        "type": "RuntimeError",
        "message": "provider\\ud800failure",
    }
    assert loaded["bytes"] == "valid\\xffbytes"
    assert loaded["nan"] == "NaN"
    assert loaded["positive"] == "Infinity"
    assert loaded["negative"] == "-Infinity"


def test_cycles_depth_and_mapping_key_collisions_fail_safe() -> None:
    cyclic: list[object] = []
    cyclic.append(cyclic)
    collision = {1: "integer", "1": "string"}

    normalized = normalize_json_safe(
        {"cycle": cyclic, "collision": collision}, max_depth=8
    )

    assert normalized["cycle"] == ["<circular reference>"]
    assert normalized["collision"] == {"1": "integer", "1#2": "string"}
    json_safe_bytes(normalized).decode("utf-8", errors="strict")


def test_sets_are_deterministic_and_max_depth_must_be_positive() -> None:
    first = json_safe_bytes({"values": {"z", "a", "m"}})
    second = json_safe_bytes({"values": {"m", "z", "a"}})

    assert first == second
    assert json.loads(first)["values"] == ["a", "m", "z"]

    try:
        normalize_json_safe({}, max_depth=0)
    except ValueError as error:
        assert "positive" in str(error)
    else:  # pragma: no cover
        raise AssertionError("invalid max_depth was accepted")
