# A-Flow

A-Flow is an independent, provider-neutral package that admits and adversarially analyzes a structured Codex plan, verifies revision closure, locks an accepted plan, detects relevant repository drift, and evaluates returned build evidence against a separately confirmed success definition.

A-Flow consumes plans and build results. It does not build products, execute plan commands, mutate the analyzed repository, call the network, invoke Docker, or invoke Edge.

## Offline local commands

From this directory, after `uv sync --offline --extra dev`:

```text
uv run --offline python -m aflow.cli analyze <analysis-request.json>
uv run --offline python -m aflow.cli close <closure-request.json>
uv run --offline python -m aflow.cli evaluate-result <build-result.json>
uv run --offline python -m aflow.cli evaluate-fixtures tests/fixtures
uv run --offline python -m aflow.cli demo
uv run --offline pytest -q
```

`close` resolves `prior-decision.json`, `original-plan.json`, and `revised-analysis-request.json` beside the closure request. `evaluate-result` resolves `locked-plan.json`, `locked-baseline.json`, `post-build-baseline.json`, `success-definition.json`, `plan.json`, and `build-evidence.json` beside the build result. It computes the post-build drift decision itself. This keeps the CLI artifact schema-exact while making every referenced artifact explicit and local.

Exit codes are: `0` success, `2` schema-invalid analysis input, `3` unresolved blocking analysis/closure gaps, `4` internal failure, `5` fixture-evaluation failure, and `6` final output not proven.

## Boundaries

- The 23 schemas under `schemas/v1` are vendored byte-for-byte from the v1 authority pack.
- Deterministic admission completes before the semantic adapter is called.
- Semantic candidates are untrusted until their requirement, plan-location, and evidence references are substantiated.
- Unsupported candidates are retained as rejected findings and cannot reduce readiness.
- Only `no_material_gap` decisions can be locked.
- Baseline capture uses read-only filesystem APIs and supports unborn/untracked repositories without invoking Git.
- Final evaluation consumes structured evidence and treats passing tests as supporting evidence, never automatic proof.
- Atomic artifact output requires an explicit output root and rejects analyzed-repository destinations.

See [lifecycle.md](docs/lifecycle.md) and [validation.md](docs/validation.md).
