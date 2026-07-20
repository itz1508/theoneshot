from __future__ import annotations

import copy
import inspect
import io
import json
import threading
import time
from pathlib import Path

import pytest

import audisor.audisor_lifecycle.ignition as ignition
from audisor.audisor_lifecycle.adapter import assemble_contract as real_assemble_contract
from audisor.audisor_lifecycle.indicator import AudisorIndicator


FIXTURES = Path(__file__).parent / "fixtures" / "aflow_contract"


class TTYBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


def source() -> dict:
    return json.loads((FIXTURES / "ready-input.json").read_text(encoding="utf-8"))


def wait_for_thread(indicator: AudisorIndicator) -> threading.Thread:
    deadline = time.monotonic() + 1
    while indicator._thread is None and time.monotonic() < deadline:
        time.sleep(0.001)
    assert indicator._thread is not None
    return indicator._thread


def test_tty_mode_starts_and_stops_indicator() -> None:
    stream = TTYBuffer()
    indicator = AudisorIndicator(stream=stream)
    with indicator:
        thread = wait_for_thread(indicator)
        assert thread.is_alive()
        deadline = time.monotonic() + 1
        while not stream.getvalue() and time.monotonic() < deadline:
            time.sleep(0.001)
        assert "Audisor checking..." in stream.getvalue()
    assert not thread.is_alive()


def test_non_tty_mode_starts_no_thread_and_emits_no_output() -> None:
    stream = io.StringIO()
    indicator = AudisorIndicator(stream=stream)
    with indicator:
        assert indicator._thread is None
    assert stream.getvalue() == ""


@pytest.mark.parametrize("failure", [False, True])
def test_indicator_thread_terminates_on_success_or_exception(failure: bool) -> None:
    stream = TTYBuffer()
    indicator = AudisorIndicator(stream=stream)
    error = RuntimeError("gap-check failed")
    if failure:
        with pytest.raises(RuntimeError) as caught:
            with indicator:
                thread = wait_for_thread(indicator)
                raise error
        assert caught.value is error
    else:
        with indicator:
            thread = wait_for_thread(indicator)
    assert not thread.is_alive()


def test_indicator_thread_terminates_on_keyboard_interrupt() -> None:
    indicator = AudisorIndicator(stream=TTYBuffer())
    with pytest.raises(KeyboardInterrupt):
        with indicator:
            thread = wait_for_thread(indicator)
            raise KeyboardInterrupt()
    assert not thread.is_alive()


def test_ignition_calls_aflow_and_assembly_once_and_preserves_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    value = source()
    expected = real_assemble_contract(value)["aflow_execution_contract"]
    calls = {"invoke": 0, "assemble": 0}

    def invoke(task: dict, candidate: dict, context: dict, **kwargs: object) -> dict:
        calls["invoke"] += 1
        result = copy.deepcopy(value)
        result["candidate_implementation_plan"] = candidate
        return result

    def assemble(value: dict) -> dict:
        calls["assemble"] += 1
        return real_assemble_contract(value)

    monkeypatch.setattr(ignition, "assemble_contract", assemble)
    result = ignition.ignite(
        task_kind="implementation",
        task={"id": "task-001"},
        repository_context={},
        supplied_plan=value["candidate_implementation_plan"],
        invoke_audisor_analysis=invoke,
    )

    assert calls == {"invoke": 1, "assemble": 1}
    assert result.execution_contract == expected


def test_ignition_failure_paths_cleanup_and_preserve_call_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    value = source()
    counts = {"invoke": 0, "assemble": 0}
    indicators: list[AudisorIndicator] = []

    def indicator_factory() -> AudisorIndicator:
        indicator = AudisorIndicator(stream=TTYBuffer())
        indicators.append(indicator)
        return indicator

    monkeypatch.setattr(ignition, "AudisorIndicator", indicator_factory)

    def invoke_failure(task: dict, candidate: dict, context: dict, **kwargs: object) -> dict:
        counts["invoke"] += 1
        raise ValueError("invoke sentinel")

    def assemble_failure(value: dict) -> dict:
        counts["assemble"] += 1
        raise KeyError("assembly sentinel")

    with pytest.raises(ValueError, match="invoke sentinel"):
        ignition.ignite(
            task_kind="implementation", task={}, repository_context={},
            supplied_plan=value["candidate_implementation_plan"], invoke_audisor_analysis=invoke_failure,
        )
    assert counts == {"invoke": 1, "assemble": 0}
    assert indicators[-1]._thread is not None and not indicators[-1]._thread.is_alive()

    def invoke_success(task: dict, candidate: dict, context: dict, **kwargs: object) -> dict:
        counts["invoke"] += 1
        result = copy.deepcopy(value)
        result["candidate_implementation_plan"] = candidate
        return result

    monkeypatch.setattr(ignition, "assemble_contract", assemble_failure)
    with pytest.raises(KeyError, match="assembly sentinel"):
        ignition.ignite(
            task_kind="implementation", task={}, repository_context={},
            supplied_plan=value["candidate_implementation_plan"], invoke_audisor_analysis=invoke_success,
        )
    assert counts == {"invoke": 2, "assemble": 1}
    assert indicators[-1]._thread is not None and not indicators[-1]._thread.is_alive()


def test_indicator_has_no_provider_agent_network_or_file_side_effects() -> None:
    source_text = inspect.getsource(AudisorIndicator)
    for forbidden in ("Fireworks", "invoke_audisor_analysis", "assemble_contract", "subprocess", "requests", "socket", "write_text"):
        assert forbidden not in source_text
