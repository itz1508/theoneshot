# Artifact lifecycle

1. Load a separately confirmed, hash-bound success definition.
2. Load a Codex plan that exactly references the success definition, authority bundle, and repository baseline.
3. Run schema admission and deterministic reference, coverage, dependency, scope, authority, phase, prerequisite, and activation checks.
4. Only after deterministic admission passes, ask a provider-neutral reviewer for semantic candidates.
5. Substantiate each candidate against bounded input evidence; quarantine unsupported candidates.
6. Emit `no_material_gap` or a blocking decision with specific findings.
7. Re-evaluate original closure predicates for a revision; a version bump or text-only change is insufficient.
8. Lock only an exact `no_material_gap` plan and re-check drift before handoff.
9. Codex or another authorized builder works outside A-Flow and returns a schema-valid build result.
10. Re-check drift after return, trace every locked requirement to evidence, assess all quality dimensions, and emit the final decision.

Lifecycle states are `draft`, `analyzed`, `revision_required`, `accepted`, `locked`, `handed_off`, `build_returned`, `evaluated`, and `invalidated`. Invalid transitions raise an error.

