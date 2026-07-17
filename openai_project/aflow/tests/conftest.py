from __future__ import annotations

from pathlib import Path

import pytest

from aflow.fixtures.factory import fixed_clock, request_bundle


@pytest.fixture
def clean_request():
    return request_bundle()


@pytest.fixture
def fixture_root() -> Path:
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def clock():
    return fixed_clock

