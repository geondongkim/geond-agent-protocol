from __future__ import annotations

import json
from pathlib import Path

from geond import orchestrator_task_planner
from geond.task_graph import normalize_task_graph_payload


def status_payload(title: str = "Implement checkout flow") -> dict[str, object]:
    return {
        "schema": "geond.orchestrator_status.v1",
        "status": "ok",
        "run": {
            "run_id": "run-1",
            "workspace_id": "workspace-1",
            "title": title,
            "status": "active",
            "risk_level": "medium",
        },
        "claimable_tasks": [
            {
                "task_id": "placeholder-1",
                "title": orchestrator_task_planner.PLANNING_PLACEHOLDER_TITLE,
                "status": "ready",
                "metadata": {},
            }
        ],
    }


def patch_graph_state(monkeypatch, tasks=None, edges=None) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        orchestrator_task_planner.orchestration_store,
        "list_task_graph",
        lambda *args, **kwargs: {
            "schema": "geond.task_graph.v1",
            "status": "ok",
            "code": None,
            "tasks": tasks
            if tasks is not None
            else [
                {
                    "task_id": "placeholder-1",
                    "title": orchestrator_task_planner.PLANNING_PLACEHOLDER_TITLE,
                    "status": "ready",
                    "metadata": {},
                }
            ],
            "edges": edges or [],
        },
    )


def test_auto_template_selection() -> None:
    assert orchestrator_task_planner.select_template("auto", "Fix failing checkout") == "bugfix"
    assert orchestrator_task_planner.select_template("auto", "Update README docs") == "docs"
    assert orchestrator_task_planner.select_template("auto", "CI workflow release") == "ops"
    assert (
        orchestrator_task_planner.select_template("auto", "Add checkout feature")
        == "implementation"
    )


def test_proposal_tasks_match_task_graph_input(monkeypatch) -> None:  # noqa: ANN001
    patch_graph_state(monkeypatch)
    payload = orchestrator_task_planner.propose_task_graph(
        object(),
        "run-1",
        template="auto",
        status_payload=status_payload("Fix checkout bug"),
    )

    assert payload["schema"] == "geond.task_graph_proposal.v1"
    assert payload["template"] == "bugfix"
    assert payload["eligible_for_materialization"] is True
    normalized = normalize_task_graph_payload(payload)
    assert [task["key"] for task in normalized["tasks"]] == [
        "repro",
        "fix",
        "validate",
        "handoff",
    ]


def test_proposal_validation_rejects_duplicate_and_missing_dependency() -> None:
    duplicate = orchestrator_task_planner.validate_task_graph_tasks(
        [
            {"key": "a", "title": "A"},
            {"key": "a", "title": "Duplicate"},
        ]
    )
    missing = orchestrator_task_planner.validate_task_graph_tasks(
        [{"key": "a", "title": "A", "depends_on": ["missing"]}]
    )
    empty = orchestrator_task_planner.validate_task_graph_tasks([])

    assert duplicate["code"] == "VALIDATION_ERROR"
    assert missing["code"] == "TASK_GRAPH_DEPENDENCY_NOT_FOUND"
    assert empty["code"] == "VALIDATION_ERROR"


def test_materialization_preview_and_execute(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    patch_graph_state(monkeypatch)
    calls: list[tuple[str, dict[str, object]]] = []
    graph_payload = {
        "tasks": [
            {"key": "design", "title": "Design", "depends_on": []},
            {"key": "implement", "title": "Implement", "depends_on": ["design"]},
        ]
    }
    source_path = tmp_path / "proposal.json"
    source_path.write_text(json.dumps(graph_payload), encoding="utf-8")

    monkeypatch.setattr(
        orchestrator_task_planner.orchestrator,
        "get_status",
        lambda *args, **kwargs: status_payload(),
    )

    def fake_create_graph(conn, run_id, tasks, **kwargs):  # noqa: ANN001, ANN202
        calls.append(("create", {"run_id": run_id, "tasks": tasks, **kwargs}))
        return {
            "schema": "geond.task_graph.v1",
            "status": "ok",
            "code": None,
            "tasks": [{"task_id": "task-1"}],
            "edges": [],
        }

    def fake_update(conn, task_id, status, **kwargs):  # noqa: ANN001, ANN202
        calls.append(("update", {"task_id": task_id, "status": status, **kwargs}))
        return {"status": "ok", "task": {"task_id": task_id, "status": status}}

    monkeypatch.setattr(
        orchestrator_task_planner.orchestration_store,
        "create_task_graph",
        fake_create_graph,
    )
    monkeypatch.setattr(
        orchestrator_task_planner.orchestration_store,
        "update_task_state",
        fake_update,
    )

    preview = orchestrator_task_planner.apply_task_graph_file(
        object(),
        "run-1",
        source_path,
    )
    executed = orchestrator_task_planner.apply_task_graph_file(
        object(),
        "run-1",
        source_path,
        execute=True,
    )

    assert preview["status"] == "preview"
    assert executed["status"] == "ok"
    assert calls[0][0] == "create"
    assert calls[1] == (
        "update",
        {
            "task_id": "placeholder-1",
            "status": "done",
            "metadata": {"source": "task_graph_planner", "materialized_graph": True},
            "idempotency_key": "task_graph:run-1:placeholder-done",
        },
    )
