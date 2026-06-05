from __future__ import annotations

import json
from pathlib import Path

from geond import orchestrator_graph_review


def patch_review_state(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        orchestrator_graph_review.orchestrator,
        "get_status",
        lambda *args, **kwargs: {"schema": "geond.orchestrator_status.v1", "status": "ok"},
    )
    monkeypatch.setattr(
        orchestrator_graph_review.orchestrator_task_planner,
        "materialization_eligibility",
        lambda *args, **kwargs: {"eligible": True, "reason": "placeholder only"},
    )


def proposal(tasks: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema": "geond.task_graph_proposal.v1",
        "status": "ok",
        "code": None,
        "run_id": "run-1",
        "proposal_id": "proposal-1",
        "planner": "llm",
        "planner_agent": "codex",
        "tasks": tasks,
    }


def task(key: str, *, depends_on: list[str] | None = None) -> dict[str, object]:
    return {
        "key": key,
        "title": key.title(),
        "depends_on": depends_on or [],
        "required_evidence": ["command"],
    }


def test_valid_llm_proposal_is_approved(monkeypatch) -> None:  # noqa: ANN001
    patch_review_state(monkeypatch)

    payload = orchestrator_graph_review.review_task_graph_proposal(
        object(),
        "run-1",
        proposal([task("design"), task("implement", depends_on=["design"])]),
    )

    assert payload["schema"] == "geond.task_graph_review.v1"
    assert payload["decision"] == "approved"
    assert payload["review_score"] == 100
    assert payload["findings"] == []


def test_invalid_graph_shapes_are_blocked(monkeypatch) -> None:  # noqa: ANN001
    patch_review_state(monkeypatch)

    duplicate = orchestrator_graph_review.review_task_graph_proposal(
        object(),
        "run-1",
        proposal([task("a"), task("a")]),
    )
    missing = orchestrator_graph_review.review_task_graph_proposal(
        object(),
        "run-1",
        proposal([{"key": "a", "title": "A", "depends_on": ["missing"]}]),
    )
    empty = orchestrator_graph_review.review_task_graph_proposal(
        object(),
        "run-1",
        proposal([]),
    )

    assert duplicate["decision"] == "blocked"
    assert missing["decision"] == "blocked"
    assert empty["decision"] == "blocked"


def test_cycle_is_blocked_and_missing_evidence_needs_revision(monkeypatch) -> None:  # noqa: ANN001
    patch_review_state(monkeypatch)

    cycle = orchestrator_graph_review.review_task_graph_proposal(
        object(),
        "run-1",
        proposal([task("a", depends_on=["b"]), task("b", depends_on=["a"])]),
    )
    evidence = orchestrator_graph_review.review_task_graph_proposal(
        object(),
        "run-1",
        proposal([{"key": "a", "title": "A", "depends_on": []}]),
    )

    assert cycle["decision"] == "blocked"
    assert cycle["findings"][0]["code"] == "TASK_GRAPH_CYCLE"
    assert evidence["decision"] == "needs_revision"
    assert evidence["findings"][0]["code"] == "REQUIRED_EVIDENCE_MISSING"


def test_latest_planner_result_is_reviewed(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    patch_review_state(monkeypatch)
    result_dir = tmp_path / "run-1" / "planner" / "invocation-1"
    result_dir.mkdir(parents=True)
    (result_dir / "RESULT.json").write_text(
        json.dumps({"task_graph_proposal": proposal([task("design")])}),
        encoding="utf-8",
    )

    payload = orchestrator_graph_review.review_latest_planner_result(
        object(),
        "run-1",
        base_dir=tmp_path,
        write_bundle=True,
    )

    assert payload["decision"] == "approved"
    assert payload["source_path"].endswith("RESULT.json")
    assert Path(payload["bundle"]["json_path"]).exists()
