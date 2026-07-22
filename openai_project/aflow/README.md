# A-Flow

A-Flow is the `theoneshot-aflow` package and `aflow` CLI. It is an independent, provider-neutral plan-readiness tool that admits and adversarially analyzes a structured Codex plan, verifies revision closure, locks an accepted plan, detects relevant repository drift, and evaluates returned build evidence against a separately confirmed success definition.

A-Flow consumes plans and build results. It does not build products, execute plan commands, mutate the analyzed repository, call the network, invoke Docker, or invoke Edge.

## CLI discovery

Always confirm the active command surface before scripted or containerized use:

```powershell
uv run aflow --help
uv run python -m aflow.cli --help
```

Current verified command surface:

```text
usage: aflow [-h] {analyze,close,evaluate-result,evaluate-fixtures,demo} ...
```

## Offline local commands

From this directory, after `uv sync --offline --extra dev`:

```powershell
uv run --offline aflow analyze <analysis-request.json>
uv run --offline aflow close <closure-request.json>
uv run --offline aflow evaluate-result <build-result.json>
uv run --offline aflow evaluate-fixtures tests/fixtures
uv run --offline aflow demo
uv run --offline pytest -q
```

Equivalent module form:

```powershell
uv run --offline python -m aflow.cli analyze <analysis-request.json>
uv run --offline python -m aflow.cli close <closure-request.json>
uv run --offline python -m aflow.cli evaluate-result <build-result.json>
uv run --offline python -m aflow.cli evaluate-fixtures tests/fixtures
uv run --offline python -m aflow.cli demo
```

`close` resolves `prior-decision.json`, `original-plan.json`, and `revised-analysis-request.json` beside the closure request. `evaluate-result` resolves `locked-plan.json`, `locked-baseline.json`, `post-build-baseline.json`, `success-definition.json`, `plan.json`, and `build-evidence.json` beside the build result. It computes the post-build drift decision itself. This keeps the CLI artifact schema-exact while making every referenced artifact explicit and local.

Exit codes are: `0` success, `2` schema-invalid analysis input, `3` unresolved blocking analysis/closure gaps, `4` internal failure, `5` fixture-evaluation failure, and `6` final output not proven.

## Submission image

The submission container image is published as:

```text
ghcr.io/itz1508/theoneshot-aflow:submission-20260721
```

Anonymous pull was verified after `docker logout ghcr.io`:

```powershell
docker pull ghcr.io/itz1508/theoneshot-aflow:submission-20260721
docker run --rm ghcr.io/itz1508/theoneshot-aflow:submission-20260721 --help
```

The submitted container exposes this download-facing command surface:

```text
usage: aflow [-h] {review,validate-bundle,setup,doctor,demo,serve,mcp,progress,resume,verify,status} ...
```

Verified digest:

```text
sha256:a98016deffb5b7bf80e5d9f27c75bcdd7552cafa1e040155e305ff6519ce880e
```

For containerized proofs, discover CLI syntax with `--help` first, then run the exact subcommand shown by the image. Do not infer stack readiness from image existence alone; readiness depends on a real A-Flow review/evaluation with persisted evidence.

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
