from __future__ import annotations

import json
from pathlib import Path

from geond import orchestrator_llm_planner


def status_payload(workspace_id: str = "workspace-1") -> dict[str, object]:
    return {
        "schema": "geond.orchestrator_status.v1",
        "status": "ok",
        "run": {
            "run_id": "run-1",
            "workspace_id": workspace_id,
            "title": "Implement checkout flow",
            "status": "active",
            "risk_level": "medium",
        },
        "readiness": {"status": "not_ready", "blocking_reasons": []},
        "claimable_tasks": [],
        "task_graph": {"tasks": [], "edges": []},
    }


def patch_workspace(monkeypatch, tmp_path: Path, root_uri: str | None = None) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        orchestrator_llm_planner.orchestrator,
        "get_status",
        lambda *args, **kwargs: status_payload(),
    )
    monkeypatch.setattr(
        orchestrator_llm_planner.orchestrator_spawn,
        "get_workspace_root_uri",
        lambda *args, **kwargs: root_uri or tmp_path.as_uri(),
    )
    monkeypatch.setattr(
        orchestrator_llm_planner.orchestrator_task_planner,
        "materialization_eligibility",
        lambda *args, **kwargs: {
            "eligible": True,
            "reason": "placeholder only",
            "planning_placeholder_task": {"task_id": "placeholder-1"},
        },
    )


def runner_with_payload(payload: object):  # noqa: ANN201
    def fake_runner(command, prompt, invocation, timeout_seconds):  # noqa: ANN001
        invocation.last_message_path.write_text(json.dumps(payload), encoding="utf-8")
        return orchestrator_llm_planner.PlannerRunResult(
            exit_code=0,
            timed_out=False,
            stdout=json.dumps(payload),
            stderr="",
            command=command,
        )

    return fake_runner


def test_llm_planner_preview_does_not_execute_or_write(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    patch_workspace(monkeypatch, tmp_path)
    monkeypatch.setattr(
        orchestrator_llm_planner.orchestrator_spawn,
        "find_agent_binary",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("preview must not resolve")),
    )

    payload = orchestrator_llm_planner.propose_task_graph_with_llm(
        object(),
        "run-1",
        base_dir=tmp_path / "runs",
    )

    assert payload["schema"] == "geond.llm_task_graph_planner.v1"
    assert payload["status"] == "preview"
    assert payload["execute_planner"] is False
    assert payload["task_graph_proposal"] is None
    assert not (tmp_path / "runs" / "run-1" / "planner").exists()


def test_llm_planner_codex_result_normalizes_to_task_graph_proposal(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    patch_workspace(monkeypatch, tmp_path)
    monkeypatch.setattr(
        orchestrator_llm_planner.orchestrator_spawn,
        "find_agent_binary",
        lambda agent: "codex",
    )

    payload = orchestrator_llm_planner.propose_task_graph_with_llm(
        object(),
        "run-1",
        execute_planner=True,
        base_dir=tmp_path / "runs",
        runner=runner_with_payload(
            {
                "tasks": [
                    {"key": "design", "title": "Design"},
                    {"key": "implement", "title": "Implement", "depends_on": ["design"]},
                ]
            }
        ),
    )

    proposal = payload["task_graph_proposal"]
    assert payload["status"] == "ok"
    assert proposal["schema"] == "geond.task_graph_proposal.v1"
    assert proposal["planner"] == "llm"
    assert proposal["planner_agent"] == "codex"
    assert [task["key"] for task in proposal["tasks"]] == ["design", "implement"]
    assert Path(payload["invocation"]["prompt_path"]).exists()
    assert Path(payload["invocation"]["result_path"]).exists()


def test_llm_planner_claude_wrapper_result_is_supported(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    patch_workspace(monkeypatch, tmp_path)
    monkeypatch.setattr(
        orchestrator_llm_planner.orchestrator_spawn,
        "find_agent_binary",
        lambda agent: "claude",
    )
    wrapped = {
        "result": json.dumps(
            {"tasks": [{"key": "inspect", "title": "Inspect operational state"}]}
        )
    }

    payload = orchestrator_llm_planner.propose_task_graph_with_llm(
        object(),
        "run-1",
        agent_name="claude",
        execute_planner=True,
        base_dir=tmp_path / "runs",
        runner=runner_with_payload(wrapped),
    )

    assert payload["status"] == "ok"
    assert payload["task_graph_proposal"]["planner_agent"] == "claude"
    assert payload["task_graph_proposal"]["tasks"][0]["key"] == "inspect"


def test_llm_planner_stable_error_codes(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    patch_workspace(monkeypatch, tmp_path)

    unsupported = orchestrator_llm_planner.propose_task_graph_with_llm(
        object(),
        "run-1",
        agent_name="copilot",
    )
    assert unsupported["code"] == "UNSUPPORTED_AGENT"

    monkeypatch.setattr(
        orchestrator_llm_planner.orchestrator_spawn,
        "find_agent_binary",
        lambda agent: None,
    )
    missing = orchestrator_llm_planner.propose_task_graph_with_llm(
        object(),
        "run-1",
        execute_planner=True,
    )
    assert missing["code"] == "CODEX_CLI_NOT_FOUND"

    patch_workspace(monkeypatch, tmp_path, root_uri="https://example.com/repo")
    remote = orchestrator_llm_planner.propose_task_graph_with_llm(object(), "run-1")
    assert remote["code"] == "WORKSPACE_NOT_LOCAL"


def test_llm_planner_invalid_json_and_validation_errors(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    patch_workspace(monkeypatch, tmp_path)
    monkeypatch.setattr(
        orchestrator_llm_planner.orchestrator_spawn,
        "find_agent_binary",
        lambda agent: "codex",
    )

    def invalid_runner(command, prompt, invocation, timeout_seconds):  # noqa: ANN001
        invocation.last_message_path.write_text("{not json", encoding="utf-8")
        return orchestrator_llm_planner.PlannerRunResult(
            exit_code=0,
            timed_out=False,
            stdout="{not json",
            stderr="",
            command=command,
        )

    invalid = orchestrator_llm_planner.propose_task_graph_with_llm(
        object(),
        "run-1",
        execute_planner=True,
        base_dir=tmp_path / "runs-invalid",
        runner=invalid_runner,
    )
    assert invalid["code"] == "PLANNER_RESULT_INVALID_JSON"

    duplicate = orchestrator_llm_planner.propose_task_graph_with_llm(
        object(),
        "run-1",
        execute_planner=True,
        base_dir=tmp_path / "runs-duplicate",
        runner=runner_with_payload(
            {
                "tasks": [
                    {"key": "a", "title": "A"},
                    {"key": "a", "title": "Duplicate"},
                ]
            }
        ),
    )
    assert duplicate["code"] == "VALIDATION_ERROR"
