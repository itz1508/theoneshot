"""Public-import smoke test driven by the compatibility matrix.

For every row in ``docs/compatibility/lifecycle-refactor-matrix.json`` the
symbol must be importable from both its historical ``artifact_flow`` path
(the retained compatibility export) and its new capability-module path.
A failure here means the refactor broke a documented contract.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

_MATRIX = (
    Path(__file__).resolve().parents[4]
    / "docs"
    / "compatibility"
    / "lifecycle-refactor-matrix.json"
)


def _load_matrix_rows() -> list[dict]:
    data = json.loads(_MATRIX.read_text(encoding="utf-8"))
    assert data.get("matrix_version") == 1
    return list(data["symbols"])


_ROWS = _load_matrix_rows()


@pytest.mark.parametrize(
    "symbol, old_path, new_path",
    [(row["symbol"], row["old_path"], row["new_path"]) for row in _ROWS],
    ids=[row["symbol"] for row in _ROWS],
)
def test_public_import_smoke(symbol: str, old_path: str, new_path: str) -> None:
    # The historical import path must still resolve the symbol — that is the
    # compatibility guarantee the matrix records.
    legacy_module = importlib.import_module(old_path)
    legacy_value = getattr(legacy_module, symbol)
    assert legacy_value is not None or symbol.startswith("_")

    # The new defining module must also expose the symbol under the same name.
    new_module = importlib.import_module(new_path)
    new_value = getattr(new_module, symbol)
    assert new_value is not None or symbol.startswith("_")

    # And both paths must resolve to the exact same object — not a copy.
    assert legacy_value is new_value, (
        f"{symbol}: compatibility export in {old_path} is not the same object "
        f"as the canonical definition in {new_path}"
    )


def test_matrix_row_count_is_nontrivial() -> None:
    # Guard against the matrix accidentally shrinking to zero rows.
    assert len(_ROWS) >= 30, (
        f"compatibility matrix shrank to {len(_ROWS)} rows; expected >= 30"
    )


def test_every_matrix_row_keeps_compatibility_export() -> None:
    # The refactor promise is that every moved symbol kept its legacy export.
    missing = [row["symbol"] for row in _ROWS if not row.get("retained_export")]
    assert not missing, (
        f"compatibility matrix records dropped exports: {missing}"
    )
