"""Deterministic compatibility manifest for the A-Flow artifact lifecycle.

The manifest freezes every externally observable lifecycle contract —
stage names and order, result statuses, stage schema/instruction/example
digests, repair policy constants, active-run TTL, public imports, the
persisted filename pattern, canonicalisation behaviour, and ten
representative result structures. Regenerating it after a refactor and
comparing against the stored fixture proves behaviour preservation.

Only runtime-generated values (timestamps, run IDs, temporary state
roots) are normalised; every contract value is captured verbatim.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import tempfile
from pathlib import Path
from typing import Any, Mapping

_TIMESTAMP_KEYS = {"started_at", "completed_at"}
_RUN_ID_KEYS = {"lifecycle_run_id"}

#: Import paths that must keep working after the refactor (includes the
#: underscore-prefixed symbol the test suite imports).
PUBLIC_IMPORTS: tuple[tuple[str, str], ...] = (
    ("audisor.audisor_lifecycle.artifact_flow", "run_artifact_lifecycle"),
    ("audisor.audisor_lifecycle.artifact_flow", "LocalStageWorker"),
    ("audisor.audisor_lifecycle.artifact_flow", "StageWorker"),
    ("audisor.audisor_lifecycle.artifact_flow", "StageOutputError"),
    ("audisor.audisor_lifecycle.artifact_flow", "PersistedResultError"),
    ("audisor.audisor_lifecycle.artifact_flow", "read_last_result"),
    ("audisor.audisor_lifecycle.artifact_flow", "_submission_digest"),
    ("audisor.audisor_lifecycle.artifact_flow", "STAGES"),
    ("audisor.audisor_lifecycle.artifact_flow", "RESULT_STATUSES"),
    ("audisor.audisor_lifecycle.artifact_flow", "STAGE_OUTPUT_SCHEMAS"),
    ("audisor.audisor_lifecycle.artifact_flow", "REPAIR_POLICY"),
    ("audisor.audisor_lifecycle.artifact_flow", "default_state_root"),
    ("audisor.audisor_lifecycle", "run_artifact_lifecycle"),
    ("audisor.audisor_lifecycle", "LocalStageWorker"),
    ("audisor.audisor_lifecycle", "StageWorker"),
    ("audisor.audisor_lifecycle", "StageOutputError"),
    ("audisor.audisor_lifecycle", "PersistedResultError"),
    ("audisor.audisor_lifecycle", "read_last_result"),
    ("audisor.audisor_lifecycle", "STAGES"),
    ("audisor.audisor_lifecycle", "RESULT_STATUSES"),
)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _trigger(**overrides: Any) -> dict[str, Any]:
    base = {
        "artifact_id": "artifact.manifest",
        "artifact_type": "plan",
        "status": "draft_complete",
        "content": "Build a widget with tests and documentation.",
        "intent": "ship the widget",
        "context": "repo has pytest; CI runs uv run pytest",
    }
    base.update(overrides)
    return base


class _ScriptedWorker:
    """Canned stage worker; list values are consumed one call at a time."""

    def __init__(self, outputs: Mapping[str, Any]):
        self.outputs = dict(outputs)

    def run_stage(self, stage_name: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        value = self.outputs[stage_name]
        if isinstance(value, list):
            return value.pop(0)
        return value


_GAP_FINDING = {"gaps": [{"gap": "no rollout step", "category": "missing"}]}
_GAP_FIXING_RESOLVED = {
    "fixes": [{"gap": "no rollout step", "fix": "added rollout step"}],
    "unresolved": [],
    "improved_artifact": "Build a widget with tests, documentation, and rollout.",
}
_GAP_FIXING_UNRESOLVED = {
    "fixes": [],
    "unresolved": [
        {
            "gap": "deployment target unknown",
            "why_unresolved": "no repository evidence names an environment",
            "required_to_resolve": ["deployment environment decision"],
            "successful_resolution": "artifact names the deployment target",
        }
    ],
    "improved_artifact": "Build a widget with tests and documentation.",
}
_EVALUATION_PASSED = {"passed": True, "findings": [], "summary": "coherent and feasible"}
_EVALUATION_FAILED = {
    "passed": False,
    "summary": "artifact contradicts repo constraints",
    "findings": [
        {
            "finding": "widget spec contradicts existing API",
            "required_to_resolve": ["align spec with API"],
            "successful_resolution": "spec matches the API contract",
        }
    ],
}
_SUCCESS_CRITERIA = {
    "criteria": [{"criterion": "pytest green", "validation": "uv run pytest exit code 0"}]
}
_FIXTURE_DESIGN = {
    "fixture_cases": [
        {"name": "happy path", "expectation": "widget builds", "criterion": "pytest green"}
    ]
}
_REFIX = {
    "fixes": [{"gap": "widget spec contradicts existing API", "fix": "aligned spec with API"}],
    "unresolved": [],
    "improved_artifact": "Build a widget with tests, documentation, rollout, and aligned API spec.",
}


def _happy_outputs() -> dict[str, Any]:
    return {
        "gap_finding": _GAP_FINDING,
        "gap_fixing": _GAP_FIXING_RESOLVED,
        "evaluation": _EVALUATION_PASSED,
        "success_criteria": _SUCCESS_CRITERIA,
        "fixture_design": _FIXTURE_DESIGN,
    }


def _normalise(value: Any, state_root: str) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key in _TIMESTAMP_KEYS and isinstance(item, str):
                out[key] = "<timestamp>"
            elif key in _RUN_ID_KEYS and isinstance(item, str) and len(item) == 32:
                out[key] = "<run-id>"
            elif isinstance(item, str) and state_root in item:
                out[key] = item.replace(state_root, "<state-root>").replace("\\", "/")
            else:
                out[key] = _normalise(item, state_root)
        return out
    if isinstance(value, list):
        return [_normalise(item, state_root) for item in value]
    return value


def _representative_results(run_artifact_lifecycle, submission_digest) -> dict[str, Any]:
    """Run the ten representative scenarios against throwaway state roots."""
    scenarios: dict[str, Any] = {}

    def run(name: str, build):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = build(root)
            scenarios[name] = _normalise(result, str(root))

    run(
        "improved_zero_repair",
        lambda root: run_artifact_lifecycle(
            _trigger(), _ScriptedWorker(_happy_outputs()), state_root=root
        ),
    )
    run(
        "unresolved_first_barrier",
        lambda root: run_artifact_lifecycle(
            _trigger(),
            _ScriptedWorker({"gap_finding": _GAP_FINDING, "gap_fixing": _GAP_FIXING_UNRESOLVED}),
            state_root=root,
        ),
    )
    run(
        "improved_after_repair_cycle",
        lambda root: run_artifact_lifecycle(
            _trigger(),
            _ScriptedWorker(
                {
                    "gap_finding": _GAP_FINDING,
                    "gap_fixing": [_GAP_FIXING_RESOLVED, dict(_REFIX)],
                    "evaluation": [_EVALUATION_FAILED, _EVALUATION_PASSED],
                    "success_criteria": _SUCCESS_CRITERIA,
                    "fixture_design": _FIXTURE_DESIGN,
                }
            ),
            state_root=root,
        ),
    )
    run(
        "unresolved_no_progress_repair",
        lambda root: run_artifact_lifecycle(
            _trigger(),
            _ScriptedWorker(
                {
                    "gap_finding": _GAP_FINDING,
                    "gap_fixing": _GAP_FIXING_RESOLVED,
                    "evaluation": _EVALUATION_FAILED,
                }
            ),
            state_root=root,
        ),
    )
    run(
        "unresolved_second_evaluation_failure",
        lambda root: run_artifact_lifecycle(
            _trigger(),
            _ScriptedWorker(
                {
                    "gap_finding": _GAP_FINDING,
                    "gap_fixing": [_GAP_FIXING_RESOLVED, dict(_REFIX)],
                    "evaluation": [_EVALUATION_FAILED, _EVALUATION_FAILED],
                }
            ),
            state_root=root,
        ),
    )

    def replay(root: Path) -> Mapping[str, Any]:
        run_artifact_lifecycle(_trigger(), _ScriptedWorker(_happy_outputs()), state_root=root)
        return run_artifact_lifecycle(
            _trigger(), _ScriptedWorker(_happy_outputs()), state_root=root
        )

    run("replay", replay)

    def active_run(root: Path) -> Mapping[str, Any]:
        artifacts = root / "artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)
        digest = submission_digest(_trigger()["content"], _trigger())
        (artifacts / "artifact.manifest.running").write_text(
            json.dumps({"lifecycle_run_id": "active123", "submission_digest": digest}),
            encoding="utf-8",
        )
        return run_artifact_lifecycle(
            _trigger(), _ScriptedWorker(_happy_outputs()), state_root=root
        )

    run("active_run_error", active_run)

    def corrupt_state(root: Path) -> Mapping[str, Any]:
        artifacts = root / "artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)
        (artifacts / "artifact.manifest-99991231T000000000000Z.json").write_text(
            "{not json", encoding="utf-8"
        )
        return run_artifact_lifecycle(
            _trigger(), _ScriptedWorker(_happy_outputs()), state_root=root
        )

    run("corrupt_persisted_state_error", corrupt_state)

    run(
        "malformed_stage_output_error",
        lambda root: run_artifact_lifecycle(
            _trigger(),
            _ScriptedWorker({"gap_finding": _GAP_FINDING, "gap_fixing": {"unexpected": "shape"}}),
            state_root=root,
        ),
    )

    def recorded_repair(root: Path) -> Mapping[str, Any]:
        outputs = _happy_outputs()
        outputs["gap_finding"] = {
            "gaps": [{"gap": "no rollout step", "category": "missing", "severity": "high"}],
            "confidence": 0.9,
        }
        return run_artifact_lifecycle(_trigger(), _ScriptedWorker(outputs), state_root=root)

    run("recorded_output_repair", recorded_repair)
    return scenarios


def _canonicalisation_cases(content_digest) -> dict[str, Any]:
    lf = "Build a widget.\nWith tests.\n"
    crlf = lf.replace("\n", "\r\n")
    trailing = "Build a widget. \nWith tests.\n"
    return {
        "lf_digest": content_digest(lf),
        "crlf_digest": content_digest(crlf),
        "lf_equals_crlf": content_digest(lf) == content_digest(crlf),
        "trailing_whitespace_digest": content_digest(trailing),
        "trailing_whitespace_differs": content_digest(trailing) != content_digest(lf),
    }


def build_manifest() -> dict[str, Any]:
    """Build the full compatibility manifest as a JSON-serialisable dict."""
    flow = importlib.import_module("audisor.audisor_lifecycle.artifact_flow")

    imports_ok: list[str] = []
    for module_name, symbol in PUBLIC_IMPORTS:
        module = importlib.import_module(module_name)
        getattr(module, symbol)
        imports_ok.append(f"{module_name}:{symbol}")

    instructions = flow._STAGE_INSTRUCTIONS
    examples = flow._STAGE_OUTPUT_EXAMPLES
    return {
        "manifest_version": 1,
        "stages": list(flow.STAGES),
        "result_statuses": list(flow.RESULT_STATUSES),
        "stage_schema_hashes": {
            stage: _sha256(_stable_json(schema))
            for stage, schema in flow.STAGE_OUTPUT_SCHEMAS.items()
        },
        "stage_instruction_hashes": {stage: _sha256(text) for stage, text in instructions.items()},
        "stage_output_example_hashes": {stage: _sha256(text) for stage, text in examples.items()},
        "repair_policy": flow.REPAIR_POLICY,
        "repairable_stages": sorted(flow._REPAIRABLE_STAGES),
        "max_repaired_fields": flow._MAX_REPAIRED_FIELDS,
        "active_run_ttl_seconds": flow._ACTIVE_RUN_TTL_SECONDS,
        "public_imports": sorted(imports_ok),
        "persisted_filename_pattern": "{sanitized_artifact_id}-{UTC %Y%m%dT%H%M%S%fZ}.json",
        "artifact_id_sanitisation": {
            "plain": flow._sanitize_artifact_id("artifact.test"),
            "specials": flow._sanitize_artifact_id("a b/c\\d:e*f"),
            "truncated_length": len(flow._sanitize_artifact_id("x" * 200)),
        },
        "canonicalisation": _canonicalisation_cases(flow._content_digest),
        "representative_results": _representative_results(
            flow.run_artifact_lifecycle, flow._submission_digest
        ),
    }


def manifest_json() -> str:
    """Stable serialisation used for both the fixture file and comparison."""
    return json.dumps(build_manifest(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
