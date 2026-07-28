"""Stage contracts for the A-Flow artifact lifecycle.

Stage names and order, result statuses, per-stage output schemas and
validators, the worker protocol, and the recorded-repair policy constants.
These values are frozen behavioural contracts: the compatibility manifest
digests them, and changing any of them changes the engine's observable
behaviour.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from jsonschema import Draft202012Validator

STAGES = (
    "gap_finding",
    "gap_fixing",
    "evaluation",
    "success_criteria",
    "fixture_design",
)

RESULT_STATUSES = ("improved", "unresolved_gap", "skip", "error")

_EVIDENCE_BASIS = {"type": "string", "minLength": 1}

_UNRESOLVED_GAP_ITEM = {
    "type": "object",
    "additionalProperties": False,
    "required": ["gap", "why_unresolved", "required_to_resolve", "successful_resolution"],
    "properties": {
        "gap": {"type": "string", "minLength": 1},
        "why_unresolved": {"type": "string", "minLength": 1},
        "required_to_resolve": {"type": "array", "items": {"type": "string", "minLength": 1}},
        "successful_resolution": {"type": "string", "minLength": 1},
    },
}

#: Every worker output is validated against its stage schema before the
#: engine consumes it; malformed content becomes an ``error`` result and can
#: never cause a stage transition.
STAGE_OUTPUT_SCHEMAS: Mapping[str, Mapping[str, Any]] = {
    "gap_finding": {
        "type": "object",
        "additionalProperties": False,
        "required": ["gaps"],
        "properties": {
            "gaps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["gap"],
                    "properties": {
                        "gap": {"type": "string", "minLength": 1},
                        "category": {
                            "type": "string",
                            "enum": ["missing", "weak", "unsupported", "contradictory", "risky"],
                        },
                        "evidence_basis": _EVIDENCE_BASIS,
                    },
                },
            },
        },
    },
    "gap_fixing": {
        "type": "object",
        "additionalProperties": False,
        "required": ["fixes", "unresolved", "improved_artifact"],
        "properties": {
            "fixes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["gap", "fix"],
                    "properties": {
                        "gap": {"type": "string", "minLength": 1},
                        "fix": {"type": "string", "minLength": 1},
                        "evidence_basis": _EVIDENCE_BASIS,
                    },
                },
            },
            "unresolved": {"type": "array", "items": _UNRESOLVED_GAP_ITEM},
            "improved_artifact": {"type": "string"},
        },
    },
    "evaluation": {
        "type": "object",
        "additionalProperties": False,
        "required": ["passed", "findings", "summary"],
        "properties": {
            "passed": {"type": "boolean"},
            "summary": {"type": "string", "minLength": 1},
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["finding"],
                    "properties": {
                        "finding": {"type": "string", "minLength": 1},
                        "evidence": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                        },
                        "required_to_resolve": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                        },
                        "successful_resolution": {"type": "string", "minLength": 1},
                    },
                },
            },
        },
    },
    "success_criteria": {
        "type": "object",
        "additionalProperties": False,
        "required": ["criteria"],
        "properties": {
            "criteria": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["criterion", "validation"],
                    "properties": {
                        "criterion": {"type": "string", "minLength": 1},
                        "validation": {"type": "string", "minLength": 1},
                    },
                },
            },
        },
    },
    "fixture_design": {
        "type": "object",
        "additionalProperties": False,
        "required": ["fixture_cases"],
        "properties": {
            "fixture_cases": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "expectation"],
                    "properties": {
                        "name": {"type": "string", "minLength": 1},
                        "expectation": {"type": "string", "minLength": 1},
                        "setup": {"type": "string"},
                        "criterion": {"type": "string"},
                    },
                },
            },
        },
    },
}

_STAGE_VALIDATORS = {
    stage: Draft202012Validator(schema) for stage, schema in STAGE_OUTPUT_SCHEMAS.items()
}


class StageWorker(Protocol):
    """Reasoning boundary: a worker only answers one stage at a time."""

    def run_stage(self, stage_name: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        ...


class StageOutputError(ValueError):
    """The worker returned content that is not a valid stage output."""


#: Optional string fields a small model may emit as "" instead of omitting.
#: The adapter drops empty optionals; required fields are never touched, so
#: the engine's strict validation still decides every transition.
_OPTIONAL_STRING_FIELDS: Mapping[str, Mapping[str, frozenset[str]]] = {
    "gap_finding": {"gaps": frozenset({"category", "evidence_basis"})},
    "gap_fixing": {"fixes": frozenset({"evidence_basis"})},
    "evaluation": {"findings": frozenset({"successful_resolution"})},
    "fixture_design": {"fixture_cases": frozenset({"setup", "criterion"})},
}

#: Stage-split repair policy: limited recorded repair is allowed only in the
#: early analytic stages; everything that authorizes or shapes the handoff
#: (evaluation, success_criteria, fixture_design) is validated strictly
#: as-emitted so a repaired response can never become authoritative output.
_REPAIRABLE_STAGES = frozenset({"gap_finding", "gap_fixing"})

#: Removals beyond this ceiling mean the response is fundamentally
#: malformed, not chatty: repair stops and the stage errors.
_MAX_REPAIRED_FIELDS = 8

#: Version marker persisted with every repaired result. Repair is
#: removal-only, so kept output plus recorded removals reconstructs the raw
#: worker output exactly.
REPAIR_POLICY = "recorded-removal-v1"
