# Code-size and responsibility policy

This is the repository's single authoritative size policy. No other policy
document may define size thresholds. Enforcement is deterministic via
`python scripts/check_size.py` (see Enforcement below).

## Measurement definitions

* **physical_lines** — total newline-delimited lines in a file.
* **symbol_span** — for Python functions, async functions, methods, and
  classes: AST `end_lineno - lineno + 1`.
* **effective_code_lines** — non-blank lines excluding comment-only lines;
  includes docstrings, decorators, multiline signatures, and declarations.
  Advisory only: no repository tool currently calculates it reliably, so
  it is not enforced.

Enforcement uses `physical_lines` and `symbol_span` only.

## Function and method policy

* target: `symbol_span` ≤ 40;
* review threshold: `symbol_span` > 60;
* hard threshold: `symbol_span` > 100;
* one function represents one operation or orchestration boundary;
* new functions above 60 fail unless explicitly excepted;
* new functions above 100 always fail without a valid exception;
* unchanged baseline functions may remain temporarily but may not grow;
* a refactored oversized function must shrink materially and must not
  remain above 100 without an exception.

## Class policy

* target: `symbol_span` ≤ 150;
* review threshold: `symbol_span` > 200;
* one class owns one cohesive stateful responsibility;
* split classes containing independently testable capabilities that do
  not need shared state.

## Production-module policy

* target: `physical_lines` ≤ 300;
* review threshold: `physical_lines` > 400;
* hard threshold: `physical_lines` > 500;
* new production files above 400 fail;
* new production files above 500 fail without a valid exception;
* existing baseline files may not grow;
* a module must be split by capability when it owns multiple
  independently testable responsibilities.

## Test policy

* test-function target: `symbol_span` ≤ 60;
* test-module review threshold: `physical_lines` > 500;
* split large tests by behaviour, contract, stage, or subsystem;
* never reduce assertions merely to reduce size;
* shared fixtures must not hide scenario intent.

## Exempt categories

Only files explicitly configured in `scripts/size_exceptions.json` under
these categories may be exempt:

* generated code;
* vendored code;
* migrations;
* declarative schemas;
* static data;
* snapshots;
* generated protocol bindings;
* lock files.

Normal production logic cannot be exempted merely because it is large.

## Enforcement

* Checker: `python scripts/check_size.py` (standard library only,
  deterministic sorted output, repository-relative POSIX paths).
* Baseline: `scripts/size_baseline.json` — versioned, human-readable,
  deterministic, symbol-aware, timestamp-free. Updated only when a
  violation shrinks, disappears, or is intentionally added through an
  explicit reviewed exception. A rename never inherits a baseline
  allowance. When a file or symbol is deleted or split, its stale
  baseline entry is removed in the same change.
* Exceptions: `scripts/size_exceptions.json` — each entry requires path,
  symbol or whole-file marker, metric, permitted limit, reason,
  temporary/permanent scope, subsystem owner, protecting validation, and
  an expiry date when temporary.
* CI behaviour:
  * target threshold: advisory;
  * review threshold: warning for unchanged baseline; failure for new or
    previously compliant code;
  * hard threshold: failure unless covered by a valid exception;
  * baseline growth, malformed configuration, stale baseline entries,
    stale exception entries, expired temporary exceptions, and unmatched
    exceptions: failure.
