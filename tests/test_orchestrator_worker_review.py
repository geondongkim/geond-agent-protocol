from __future__ import annotations

import json
from pathlib import Path

from geond import orchestrator_spawn, orchestrator_worker_review


def test_copilot_worker_runs_plan_review_then_implementation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    invocation = orchestrator_spawn.new_invocation("run-1", tmp_path)
    calls: list[str] = []

    def fake_runner(command, prompt, invocation, timeout_seconds):  # noqa: ANN001, ANN202
        calls.append("plan" if "Planning Phase" in prompt else "implementation")
        if "Planning Phase" in prompt:
            invocation.last_message_path.write_text(
                json.dumps(
                    {
                        "summary": "Plan it.",
                        "steps": ["edit file", "run tests"],
                        "files_to_change": ["src/example.py"],
                        "validation_commands": ["uv run pytest"],
                        "risks": [],
                    }
                ),
                encoding="utf-8",
            )
        else:
            invocation.result_path.write_text(
                json.dumps(
                    {
                        "task_status": "done",
                        "summary": "Implemented.",
                        "tested_commands": [{"command": "uv run pytest", "exit_code": 0}],
                        "changed_files": ["src/example.py"],
                        "risks": [],
                        "next_action": "finalize",
                    }
                ),
                encoding="utf-8",
            )
        return orchestrator_spawn.CodexRunResult(
            exit_code=0,
            timed_out=False,
            stdout="",
            stderr="",
            command=command,
        )

    monkeypatch.setattr(
        orchestrator_worker_review,
        "review_payload_with_codex",
        lambda **kwargs: {
            "schema": orchestrator_worker_review.WORKER_REVIEW_SCHEMA,
            "status": "ok",
            "code": None,
            "decision": "approved",
            "summary": f"{kwargs['stage']} approved",
            "findings": [],
            "recommended_next_action": "continue",
        },
    )

    result = orchestrator_worker_review.run_copilot_with_senior_review(
        command=["copilot"],
        prompt="worker prompt",
        invocation=invocation,
        timeout_seconds=10,
        workspace_path=str(tmp_path),
        selected_task={"task_id": "task-1"},
        runner=fake_runner,
    )

    assert result.exit_code == 0
    assert calls == ["plan", "implementation"]
    assert result.metadata["worker_review"]["decision"] == "approved"
    assert json.loads(invocation.result_path.read_text())["task_status"] == "done"


def test_copilot_worker_blocks_when_plan_review_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    invocation = orchestrator_spawn.new_invocation("run-1", tmp_path)

    def fake_runner(command, prompt, invocation, timeout_seconds):  # noqa: ANN001, ANN202
        invocation.result_path.write_text(
            json.dumps(
                {
                    "summary": "Risky plan.",
                    "steps": ["edit everything"],
                    "files_to_change": ["*"],
                    "validation_commands": [],
                    "risks": ["too broad"],
                }
            ),
            encoding="utf-8",
        )
        return orchestrator_spawn.CodexRunResult(
            exit_code=0,
            timed_out=False,
            stdout="",
            stderr="",
            command=command,
        )

    monkeypatch.setattr(
        orchestrator_worker_review,
        "review_payload_with_codex",
        lambda **kwargs: {
            "schema": orchestrator_worker_review.WORKER_REVIEW_SCHEMA,
            "status": "ok",
            "code": None,
            "decision": "blocked",
            "summary": "Plan is too broad.",
            "findings": ["scope is too broad"],
            "recommended_next_action": "narrow the task",
        },
    )

    result = orchestrator_worker_review.run_copilot_with_senior_review(
        command=["copilot"],
        prompt="worker prompt",
        invocation=invocation,
        timeout_seconds=10,
        workspace_path=str(tmp_path),
        selected_task={"task_id": "task-1"},
        runner=fake_runner,
    )

    payload = json.loads(invocation.result_path.read_text())
    assert result.metadata["worker_review"]["decision"] == "blocked"
    assert payload["task_status"] == "blocked"
    assert payload["risks"] == ["scope is too broad"]


def test_review_parser_accepts_noisy_json(tmp_path: Path) -> None:
    invocation = orchestrator_spawn.new_invocation("run-1", tmp_path)
    invocation.events_path.parent.mkdir(parents=True, exist_ok=True)
    invocation.events_path.write_text(
        'noise before {"decision":"approved","summary":"ok","findings":[],'
        '"recommended_next_action":"finish"} trailing noise',
        encoding="utf-8",
    )

    payload = orchestrator_worker_review.parse_review_result(invocation)

    assert payload["status"] == "ok"
    assert payload["decision"] == "approved"
