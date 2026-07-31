# Gap Record - AFR-03 Stage Schema Delivery and Evidence

Append-only record. Plan: `../plan.md`.

### 2026-07-30T23:53:09Z - confirmed
Gap: Providers receive a generic JSON-object request instead of the actual stage schema, and precise validator evidence is discarded.
Actor: Codex CLI
State: confirmed
Evidence at this point: persisted missing-`gaps` artifacts plus `local.py` and `stage_worker.py` call-path inspection recorded in the plan's Root Cause.
Note: Repair plan supplies the full schema for native and prompt-validated modes while preserving strict required-field enforcement and bounded path evidence.
### 2026-07-31T00:08:51Z - in_progress
Implementation began fixture-first; canonical post-response validation remains authoritative and required fields will not be synthesized.

### 2026-07-31T04:48:19Z - completed
Gap closure: each stage prompt carries the complete canonical schema, native mode is selected only for a compatible declared capability, strict canonical validation remains mandatory, and bounded validator evidence is preserved. Schema fixtures and the complete runtime suite completed with exit code 0.

### 2026-07-31T05:09:57Z - validated
Phase proof: exact native-schema delivery, explicit prompt fallback, complete prompt schema, missing-required-field evidence, native invalidation, prompt-readiness preservation, distinct non-JSON classification, and single-transition fixtures completed. Integrated runtime command `python -m pytest openai_project/runtime/tests -q` exited `0` with 774 completed tests and 61 skips.
