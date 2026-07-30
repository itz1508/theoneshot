from __future__ import annotations

from operation_controller.adapters.review import CanonicalAflowReviewAdapter


def test_improved_result_continues_with_improved_plan(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "operation_controller.adapters.review.run_artifact_lifecycle",
        lambda *args, **kwargs: {
            "status": "improved",
            "lifecycle_run_id": "run-improved",
            "improved_artifact": '{"steps": ["verified"]}',
        },
    )
    result = CanonicalAflowReviewAdapter(state_root=tmp_path).review(
        {"steps": ["draft"]}, "op-1"
    )
    assert result["decision"] == "no_material_gap"
    assert result["improved_plan"] == {"steps": ["verified"]}


def test_unresolved_gap_suspends_without_system_issue(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "operation_controller.adapters.review.run_artifact_lifecycle",
        lambda *args, **kwargs: {
            "status": "unresolved_gap",
            "lifecycle_run_id": "run-gap",
            "unresolved_gaps": [{"gap": "needs evidence"}],
        },
    )
    result = CanonicalAflowReviewAdapter(state_root=tmp_path).review({}, "op-2")
    assert result["decision"] == "unresolved_gap"
    assert result["issue_id"] is None


def test_error_links_workspace_issue(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "operation_controller.adapters.review.run_artifact_lifecycle",
        lambda *args, **kwargs: {
            "status": "error",
            "stage": "gap_finding",
            "detail": "timeout",
            "lifecycle_run_id": "run-error",
        },
    )
    monkeypatch.setattr(
        "operation_controller.adapters.review.list_issues",
        lambda **kwargs: {
            "items": [{
                "issue_id": "aflow-00000000000000000000",
                "lifecycle_run_id": "run-error",
            }]
        },
    )
    result = CanonicalAflowReviewAdapter(state_root=tmp_path).review({}, "op-3")
    assert result["decision"] == "provider_error"
    assert result["issue_id"] == "aflow-00000000000000000000"
