from __future__ import annotations

import json
from pathlib import Path

import audisor.audisor_lifecycle.ignition as ignition
from audisor.audisor_lifecycle.operation import FrozenAudisorPolicy


FIXTURES = Path(__file__).parent / "fixtures" / "aflow_contract"


def test_disabled_ignition_skips_callback_and_contract(monkeypatch, tmp_path):
    calls = {"invoke": 0, "assemble": 0}
    monkeypatch.setattr(ignition, "assemble_contract", lambda value: calls.__setitem__("assemble", calls["assemble"] + 1))
    result = ignition.ignite(policy=FrozenAudisorPolicy(False, "local-openai-compatible", "qwen2.5-coder:7b", "http://127.0.0.1:11434"), task_kind="implementation", task={}, repository_context={}, supplied_plan={"plan": "candidate"}, invoke_audisor_analysis=lambda *a, **k: calls.__setitem__("invoke", calls["invoke"] + 1))
    assert not result.lifecycle_selected
    assert calls == {"invoke": 0, "assemble": 0}


def test_enabled_ignition_uses_existing_path_once(monkeypatch):
    value = json.loads((FIXTURES / "ready-input.json").read_text(encoding="utf-8"))
    calls = {"invoke": 0, "assemble": 0}

    def invoke(task, candidate, context, **kwargs):
        calls["invoke"] += 1
        result = dict(value)
        result["candidate_implementation_plan"] = candidate
        return result

    original = ignition.assemble_contract

    def assemble(value):
        calls["assemble"] += 1
        return original(value)

    monkeypatch.setattr(ignition, "assemble_contract", assemble)
    result = ignition.ignite(policy=FrozenAudisorPolicy(True, "local-openai-compatible", "qwen2.5-coder:7b", "http://127.0.0.1:11434"), task_kind="implementation", task={}, repository_context={}, supplied_plan=value["candidate_implementation_plan"], invoke_audisor_analysis=invoke)
    assert result.lifecycle_selected and calls == {"invoke": 1, "assemble": 1}
