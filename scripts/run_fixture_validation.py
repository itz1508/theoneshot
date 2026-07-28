"""Fixture validation runner.

Enumerates the 26 fixture cases defined by the task acceptance section
and drives each one through pytest. Exits non-zero when any case fails,
so the runner can be wired into CI or invoked manually as a single
command that proves the full fixture suite is green.

Fixture map
-----------

Size-checker fixtures (scripts/tests/):
  1.  Policy discovery                       test_fixture_policy_discovery
  2.  New oversized production file          test_check_size::TestNewViolations::test_new_oversized_module_fails
  3.  Unchanged baseline violation           test_check_size::TestBaselineRatchet::test_unchanged_baseline_warns_but_passes
  4.  Growing baseline violation             test_check_size::TestBaselineRatchet::test_increased_baseline_fails
  5.  Shrinking baseline violation           test_check_size::TestBaselineRatchet::test_reduced_baseline_passes_with_update_hint
  6.  New oversized function                 test_check_size::TestNewViolations::test_new_oversized_function_fails
  7.  Exempt declarative file                test_check_size::TestExemptAndExceptions::test_exempt_file_never_fails
  8.  Malformed baseline                     test_check_size::TestMalformedConfiguration::test_invalid_baseline_json_exits_2
  9.  Stale baseline entry                   test_check_size::TestBaselineRatchet::test_stale_baseline_entry_fails
 10.  Expired exception                      test_check_size::TestExemptAndExceptions::test_expired_temporary_exception_fails
 11.  Renamed file cannot evade policy       test_check_size::TestBaselineRatchet::test_rename_does_not_inherit_baseline

Lifecycle fixtures (openai_project/runtime/tests/audisor_lifecycle/):
 12.  Lifecycle happy path compatibility     test_artifact_trigger_and_barrier
 13.  First unresolved barrier                test_artifact_trigger_and_barrier (unresolved case)
 14.  Successful evaluation repair            test_artifact_evaluation_repair::test_repair_cycle_recovers_and_records_evidence
 15.  Evaluation repair makes no progress     test_artifact_evaluation_repair::test_repair_cycle_no_progress_is_unresolved
 16.  Changed artifact but deficiency remains test_artifact_evaluation_repair::test_repair_cycle_second_evaluation_still_deficient
 17.  Replay compatibility                   test_artifact_identity_and_replay::TestIdempotentReplay::test_unchanged_resubmission_replays_without_new_lifecycle
 18.  Same content with changed context       test_artifact_identity_and_replay::TestRuntimeDerivedIdentity::test_same_content_new_context_keeps_revision_with_new_run
 19.  Canonicalisation compatibility          test_artifact_identity_and_replay::TestRuntimeDerivedIdentity::test_line_ending_only_change_replays_not_reruns
 20.  Persistence compatibility               test_artifact_persistence
 21.  Corrupt newest state                    test_artifact_persistence::test_corrupt_newest_persisted_result_is_bounded_error
 22.  Active-run compatibility                test_artifact_active_runs
 23.  Stage repair compatibility              test_artifact_output_repair
 24.  Compatibility manifest                  test_compatibility_manifest
 25.  Import compatibility                    test_public_import_smoke
 26.  Full repository validation              (driven by this runner: size-check + full pytest)
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

FIXTURES: tuple[tuple[int, str, str, str], ...] = (
    (1, "Policy discovery",
     "scripts",
     "tests/test_fixture_policy_discovery.py"),
    (2, "New oversized production file",
     "scripts",
     "tests/test_check_size.py::TestNewViolations::test_new_oversized_module_fails"),
    (3, "Unchanged baseline violation",
     "scripts",
     "tests/test_check_size.py::TestBaselineRatchet::test_unchanged_baseline_warns_but_passes"),
    (4, "Growing baseline violation",
     "scripts",
     "tests/test_check_size.py::TestBaselineRatchet::test_increased_baseline_fails"),
    (5, "Shrinking baseline violation",
     "scripts",
     "tests/test_check_size.py::TestBaselineRatchet::test_reduced_baseline_passes_with_update_hint"),
    (6, "New oversized function",
     "scripts",
     "tests/test_check_size.py::TestNewViolations::test_new_oversized_function_fails"),
    (7, "Exempt declarative file",
     "scripts",
     "tests/test_check_size.py::TestExemptAndExceptions::test_exempt_file_never_fails"),
    (8, "Malformed baseline",
     "scripts",
     "tests/test_check_size.py::TestMalformedConfiguration::test_invalid_baseline_json_exits_2"),
    (9, "Stale baseline entry",
     "scripts",
     "tests/test_check_size.py::TestBaselineRatchet::test_stale_baseline_entry_fails"),
    (10, "Expired exception",
     "scripts",
     "tests/test_check_size.py::TestExemptAndExceptions::test_expired_temporary_exception_fails"),
    (11, "Renamed file cannot evade policy",
     "scripts",
     "tests/test_check_size.py::TestBaselineRatchet::test_rename_does_not_inherit_baseline"),
    (12, "Lifecycle happy path compatibility",
     "runtime",
     "tests/audisor_lifecycle/test_artifact_trigger_and_barrier.py"),
    (13, "First unresolved barrier",
     "runtime",
     "tests/audisor_lifecycle/test_artifact_trigger_and_barrier.py"),
    (14, "Successful evaluation repair",
     "runtime",
     "tests/audisor_lifecycle/test_artifact_evaluation_repair.py::TestEvaluationRepairCycle::test_cycle_recovers_and_produces_improved"),
    (15, "Evaluation repair makes no progress",
     "runtime",
     "tests/audisor_lifecycle/test_artifact_evaluation_repair.py::TestEvaluationRepairCycle::test_repair_cycle_making_no_digest_progress_is_unresolved"),
    (16, "Changed artifact but deficiency remains",
     "runtime",
     "tests/audisor_lifecycle/test_artifact_evaluation_repair.py::TestEvaluationRepairCycle::test_second_evaluation_failure_is_unresolved_with_null_downstream"),
    (17, "Replay compatibility",
     "runtime",
     "tests/audisor_lifecycle/test_artifact_identity_and_replay.py::TestIdempotentReplay::test_unchanged_resubmission_replays_without_new_lifecycle"),
    (18, "Same content with changed context",
     "runtime",
     "tests/audisor_lifecycle/test_artifact_identity_and_replay.py::TestRuntimeDerivedIdentity::test_same_content_new_context_keeps_revision_with_new_run"),
    (19, "Canonicalisation compatibility",
     "runtime",
     "tests/audisor_lifecycle/test_artifact_identity_and_replay.py::TestRuntimeDerivedIdentity::test_line_ending_only_change_replays_not_reruns"),
    (20, "Persistence compatibility",
     "runtime",
     "tests/audisor_lifecycle/test_artifact_persistence.py"),
    (21, "Corrupt newest state",
     "runtime",
     "tests/audisor_lifecycle/test_artifact_persistence.py::TestPersistedStateIntegrity::test_corrupt_persisted_result_is_a_bounded_error"),
    (22, "Active-run compatibility",
     "runtime",
     "tests/audisor_lifecycle/test_artifact_active_runs.py"),
    (23, "Stage repair compatibility",
     "runtime",
     "tests/audisor_lifecycle/test_artifact_output_repair.py"),
    (24, "Compatibility manifest",
     "runtime",
     "tests/audisor_lifecycle/test_compatibility_manifest.py"),
    (25, "Import compatibility",
     "runtime",
     "tests/audisor_lifecycle/test_public_import_smoke.py"),
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
_RUNTIME_DIR = _REPO_ROOT / "openai_project" / "runtime"


def _run_pytest(cwd: Path, args: list[str]) -> int:
    """Run pytest under uv from the runtime project (which owns pytest)."""
    cmd = [
        "uv", "run", "--directory", str(_RUNTIME_DIR), "--no-sync",
        "python", "-m", "pytest", "-q", "--no-header",
    ] + args
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


def _run_size_checker() -> int:
    """Run the deterministic size checker against the repository."""
    cmd = [
        "uv", "run", "--directory", str(_REPO_ROOT), "--no-sync",
        "python", "scripts/check_size.py",
    ]
    result = subprocess.run(cmd, cwd=_REPO_ROOT, capture_output=True, text=True)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


def _run_full_repository_suite() -> int:
    """Run the full runtime test suite (the repository's broadest suite)."""
    return _run_pytest(_RUNTIME_DIR, ["-q"])


def main() -> int:
    failures: list[tuple[int, str]] = []
    print(f"Fixture runner: {len(FIXTURES)} cases to run\n")
    for number, title, area, node in FIXTURES:
        print(f"[{number:02d}/26] {title}")
        if area == "scripts":
            # Run from runtime so pytest is available; pass absolute path
            # to the scripts-side test node.
            code = _run_pytest(_SCRIPTS_DIR, [str(_SCRIPTS_DIR / node)])
        elif area == "runtime":
            code = _run_pytest(_RUNTIME_DIR, [node])
        else:
            raise SystemExit(f"unknown fixture area: {area}")
        if code != 0:
            failures.append((number, title))
            print(f"  -> FAILED (exit {code})\n")
        else:
            print(f"  -> ok\n")

    print("Fixture 26 - Full repository validation")
    print("  size-checker pass:")
    size_code = _run_size_checker()
    if size_code != 0:
        failures.append((26, "size-checker step of full repository validation"))
        print("  -> FAILED\n")
    else:
        print("  -> ok\n")

    print("  full runtime suite:")
    suite_code = _run_pytest(_RUNTIME_DIR, ["-q"])
    if suite_code != 0:
        failures.append((26, "full-suite step of full repository validation"))
        print("  -> FAILED\n")
    else:
        print("  -> ok\n")

    print("=" * 72)
    if failures:
        print(f"FAILED: {len(failures)} fixture(s) did not pass:")
        for number, title in failures:
            print(f"  {number:02d}. {title}")
        return 1
    print(f"PASSED: all 26 fixture cases green.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
