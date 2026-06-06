from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from geond import degraded_ledger, orchestrator, orchestrator_spawn


def spawn_status(task_id: str = "task-1", task_count: int = 1) -> dict[str, object]:
    tasks = [
        {
            "task_id": task_id if index == 0 else f"task-{index + 1}",
            "title": f"Implement task {index + 1}",
            "description": "Do the work",
            "status": "ready",
        }
        for index in range(task_count)
    ]
    return {
        "schema": "geond.orchestrator_status.v1",
        "status": "ok",
        "run": {
            "run_id": "run-1",
            "workspace_id": "00000000-0000-0000-0000-000000000001",
            "title": "Fix checkout flow",
            "risk_level": "medium",
        },
        "readiness": {"status": "not_ready", "blocking_reasons": ["no completed tasks"]},
        "claimable_tasks": tasks,
        "open_findings": [],
        "pending_approvals": [],
        "latest_decisions": [],
    }


def patch_spawn_context(
    monkeypatch,
    tmp_path: Path,
    *,
    claimable: bool = True,
    task_count: int = 1,
) -> None:
    status = spawn_status(task_count=task_count)
    if not claimable:
        status["claimable_tasks"] = []
    monkeypatch.setattr(orchestrator, "get_status", lambda *args, **kwargs: status)
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "summarize_run",
        lambda conn, run_id: {"status": "ok", "markdown": "# Summary\n"},
    )
    monkeypatch.setattr(
        orchestrator_spawn,
        "get_workspace_root_uri",
        lambda conn, workspace_id: tmp_path.as_uri(),
    )
    monkeypatch.setattr(orchestrator_spawn, "find_codex_binary", lambda: "/bin/codex")


def test_resolve_local_workspace_path_handles_file_and_plain_paths(tmp_path: Path) -> None:
    from_file = orchestrator_spawn.resolve_local_workspace_path(tmp_path.as_uri())
    from_plain = orchestrator_spawn.resolve_local_workspace_path(str(tmp_path))
    remote = orchestrator_spawn.resolve_local_workspace_path("https://example.test/repo")

    assert from_file["status"] == "ok"
    assert from_plain["workspace_path"] == str(tmp_path)
    assert remote["code"] == "WORKSPACE_NOT_LOCAL"


def test_spawn_dry_run_does_not_touch_worker_storage(monkeypatch, tmp_path: Path) -> None:
    patch_spawn_context(monkeypatch, tmp_path)

    def fail_write(*args, **kwargs):  # noqa: ANN001, ANN202
        raise AssertionError("dry-run must not write worker state")

    monkeypatch.setattr(orchestrator.orchestration_store, "register_worker_session", fail_write)
    monkeypatch.setattr(orchestrator.orchestration_store, "claim_task", fail_write)
    monkeypatch.setattr(orchestrator.orchestration_store, "record_command_evidence", fail_write)
    monkeypatch.setattr(orchestrator.orchestration_store, "finish_task_with_handoff", fail_write)

    payload = orchestrator.dispatch_spawn(object(), run_id="run-1", manifest_base_dir=tmp_path)

    assert payload["status"] == "ok"
    assert payload["execution_status"] == "preview"
    assert payload["selected_task"]["task_id"] == "task-1"
    assert payload["expected_output_schema"]["required"] == [
        "task_status",
        "summary",
        "tested_commands",
        "changed_files",
        "risks",
        "next_action",
    ]
    assert not Path(payload["invocation"]["prompt_path"]).exists()


def test_spawn_write_bundle_writes_prompt_only(monkeypatch, tmp_path: Path) -> None:
    patch_spawn_context(monkeypatch, tmp_path)

    payload = orchestrator.dispatch_spawn(
        object(),
        run_id="run-1",
        write_bundle=True,
        manifest_base_dir=tmp_path,
    )

    assert payload["execution_status"] == "preview"
    assert Path(payload["invocation"]["prompt_path"]).exists()
    assert Path(payload["invocation"]["output_schema_path"]).exists()
    assert not Path(payload["invocation"]["events_path"]).exists()


def test_spawn_parallel_dry_run_assigns_multiple_agents_without_writes(
    monkeypatch,
    tmp_path: Path,
) -> None:
    patch_spawn_context(monkeypatch, tmp_path, task_count=2)
    monkeypatch.setattr(
        orchestrator_spawn,
        "find_agent_binary",
        lambda agent_name: f"/bin/{agent_name}",
    )

    def fail_write(*args, **kwargs):  # noqa: ANN001, ANN202
        raise AssertionError("parallel dry-run must not write worker state")

    monkeypatch.setattr(orchestrator.orchestration_store, "register_worker_session", fail_write)
    monkeypatch.setattr(orchestrator.orchestration_store, "claim_task", fail_write)
    monkeypatch.setattr(orchestrator.orchestration_store, "record_command_evidence", fail_write)
    monkeypatch.setattr(orchestrator.orchestration_store, "finish_task_with_handoff", fail_write)

    payload = orchestrator.dispatch_spawn(
        object(),
        run_id="run-1",
        task_ids=["task-1", "task-2"],
        agent_names=["codex", "claude"],
        max_workers=2,
        manifest_base_dir=tmp_path,
    )

    assert payload["status"] == "ok"
    assert payload["overall_execution_status"] == "preview"
    assert len(payload["items"]) == 2
    assert [item["agent_name"] for item in payload["items"]] == ["codex", "claude"]
    assert [item["selected_task"]["task_id"] for item in payload["items"]] == ["task-1", "task-2"]
    assert payload["items"][0]["invocation"]["display_command"]


def test_copilot_spawn_dry_run_uses_gh_fallback_and_planned_worktree(
    monkeypatch,
    tmp_path: Path,
) -> None:
    patch_spawn_context(monkeypatch, tmp_path)
    monkeypatch.setattr(
        orchestrator_spawn,
        "find_agent_binary",
        lambda agent_name: "/opt/homebrew/bin/gh" if agent_name == "copilot" else "/bin/codex",
    )

    payload = orchestrator.dispatch_spawn(
        object(),
        run_id="run-1",
        agent_name="copilot",
        manifest_base_dir=tmp_path,
    )

    assert payload["status"] == "ok"
    assert payload["execution_status"] == "preview"
    assert payload["agent_name"] == "copilot"
    assert "gh copilot -- -p" in payload["invocation"]["display_command"]
    assert payload["workspace"]["workspace_isolation"]["mode"] == "git_worktree"
    assert payload["workspace"]["workspace_isolation"]["created"] is False
    assert payload["workspace"]["workspace_path"].endswith(payload["invocation"]["invocation_id"])
    assert str(payload["invocation"]["result_path"]) in payload["worker_prompt"]


def test_copilot_command_builder_supports_direct_binary_and_model(tmp_path: Path) -> None:
    invocation = orchestrator_spawn.new_invocation("run-1", tmp_path)
    direct = orchestrator_spawn.build_agent_command(
        agent_name="copilot",
        agent_bin="/usr/local/bin/copilot",
        workspace_path=str(tmp_path),
        invocation=invocation,
        model="gpt-5",
    )
    via_gh = orchestrator_spawn.build_agent_command(
        agent_name="copilot",
        agent_bin="/opt/homebrew/bin/gh",
        workspace_path=str(tmp_path),
        invocation=invocation,
    )

    assert "copilot -p" in direct[-1]
    assert "--model gpt-5" in direct[-1]
    assert "gh copilot -- -p" in via_gh[-1]
    assert "--allow-tool=write" in via_gh[-1]
    assert "--allow-tool=shell" in via_gh[-1]
    assert "git push" in via_gh[-1]


def test_worker_result_parser_prefers_result_json_and_extracts_noisy_stdout(
    tmp_path: Path,
) -> None:
    invocation = orchestrator_spawn.new_invocation("run-1", tmp_path)
    invocation.output_dir.mkdir(parents=True, exist_ok=True)
    invocation.last_message_path.write_text(
        json.dumps(
            {
                "task_status": "blocked",
                "summary": "old",
                "tested_commands": [],
                "changed_files": [],
                "risks": ["old"],
                "next_action": "retry",
            }
        ),
        encoding="utf-8",
    )
    invocation.result_path.write_text(
        json.dumps(
            {
                "task_status": "done",
                "summary": "from result path",
                "tested_commands": [],
                "changed_files": [],
                "risks": [],
                "next_action": "finish",
            }
        ),
        encoding="utf-8",
    )

    parsed = orchestrator_spawn.parse_worker_result(invocation)

    noisy = orchestrator_spawn.new_invocation("run-2", tmp_path)
    noisy.output_dir.mkdir(parents=True, exist_ok=True)
    noisy.events_path.write_text(
        'before {"task_status":"done","summary":"from stdout","tested_commands":[],'
        '"changed_files":[],"risks":[],"next_action":"finish"} after',
        encoding="utf-8",
    )

    noisy_parsed = orchestrator_spawn.parse_worker_result(noisy)

    assert parsed["result"]["summary"] == "from result path"
    assert noisy_parsed["status"] == "ok"
    assert noisy_parsed["result"]["summary"] == "from stdout"


def test_real_copilot_prompt_mode_smoke(tmp_path: Path) -> None:
    if os.environ.get("GEOND_RUN_REAL_COPILOT_SMOKE") != "1":
        pytest.skip("Set GEOND_RUN_REAL_COPILOT_SMOKE=1 to run real Copilot CLI smoke.")
    agent_bin = orchestrator_spawn.find_agent_binary("copilot")
    assert agent_bin, "Copilot CLI was not found."

    invocation = orchestrator_spawn.new_invocation("real-copilot-smoke", tmp_path)
    command = orchestrator_spawn.build_agent_command(
        agent_name="copilot",
        agent_bin=agent_bin,
        workspace_path=str(tmp_path),
        invocation=invocation,
    )
    prompt = (
        f"Write this exact JSON object to {invocation.result_path}: "
        '{"task_status":"done","summary":"copilot smoke ok","tested_commands":[],'
        '"changed_files":[],"risks":[],"next_action":"none"}. '
        "Your final response may be brief."
    )

    result = orchestrator_spawn.run_codex(
        command=command,
        prompt=prompt,
        invocation=invocation,
        timeout_seconds=120,
    )
    parsed = orchestrator_spawn.parse_worker_result(invocation)

    assert result.exit_code == 0, result.stderr or result.stdout
    assert parsed["status"] == "ok"
    assert parsed["result"]["summary"]


def test_spawn_execute_records_evidence_then_finishes(monkeypatch, tmp_path: Path) -> None:
    patch_spawn_context(monkeypatch, tmp_path)
    calls: list[str] = []

    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "register_worker_session",
        lambda *args, **kwargs: (
            calls.append("register")
            or {
                "status": "ok",
                "worker_session": {
                    "worker_session_id": "worker-1",
                    "run_id": "run-1",
                    "agent_name": "codex",
                },
            }
        ),
    )
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "claim_task",
        lambda *args, **kwargs: (
            calls.append("claim")
            or {
                "status": "ok",
                "lease": {"lease_id": "lease-1"},
                "task": {"task_id": "task-1"},
            }
        ),
    )

    def fake_record(*args, **kwargs):  # noqa: ANN001, ANN202
        calls.append("evidence")
        assert kwargs["task_id"] == "task-1"
        assert args[2] == "uv run pytest"
        return {
            "status": "ok",
            "command_evidence": {"command_evidence_id": "evidence-1"},
        }

    def fake_finish(*args, **kwargs):  # noqa: ANN001, ANN202
        calls.append("finish")
        assert kwargs["task_status"] == "done"
        assert kwargs["tested_commands"] == ["uv run pytest"]
        return {
            "status": "ok",
            "handoff_id": "handoff-1",
            "task": {"task_id": "task-1", "status": "done"},
        }

    monkeypatch.setattr(orchestrator.orchestration_store, "record_command_evidence", fake_record)
    monkeypatch.setattr(orchestrator.orchestration_store, "finish_task_with_handoff", fake_finish)

    def fake_runner(command, prompt, invocation, timeout_seconds):  # noqa: ANN001, ANN202
        invocation.last_message_path.write_text(
            json.dumps(
                {
                    "task_status": "done",
                    "summary": "Implemented task.",
                    "tested_commands": [
                        {
                            "command": "uv run pytest",
                            "purpose": "full test suite",
                            "status": "passed",
                            "exit_code": 0,
                            "stdout_summary": "passed",
                            "stderr_summary": "",
                        }
                    ],
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

    payload = orchestrator.dispatch_spawn(
        object(),
        run_id="run-1",
        execute=True,
        manifest_base_dir=tmp_path,
        codex_runner=fake_runner,
    )

    assert payload["status"] == "ok"
    assert payload["execution_status"] == "completed"
    assert calls == ["register", "claim", "evidence", "finish"]
    assert Path(payload["invocation"]["result_path"]).exists()


def test_copilot_execute_requires_senior_review_before_finish(
    monkeypatch,
    tmp_path: Path,
) -> None:
    patch_spawn_context(monkeypatch, tmp_path)
    calls: list[str] = []

    monkeypatch.setattr(
        orchestrator_spawn,
        "find_agent_binary",
        lambda agent_name: "/opt/homebrew/bin/gh" if agent_name == "copilot" else "/bin/codex",
    )
    monkeypatch.setattr(
        orchestrator_spawn,
        "prepare_git_worktree",
        lambda **kwargs: {
            "status": "ok",
            "code": None,
            "mode": "git_worktree",
            "source_workspace_path": kwargs["source_workspace_path"],
            "worktree_path": str(kwargs["worktree_path"]),
            "branch_name": kwargs["branch_name"],
            "created": kwargs["create"],
        },
    )
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "register_worker_session",
        lambda *args, **kwargs: (
            calls.append("register")
            or {"status": "ok", "worker_session": {"worker_session_id": "worker-copilot"}}
        ),
    )
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "claim_task",
        lambda *args, **kwargs: (
            calls.append("claim") or {"status": "ok", "lease": {"lease_id": "lease-1"}}
        ),
    )
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "record_command_evidence",
        lambda *args, **kwargs: calls.append("evidence") or {"status": "ok"},
    )
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "finish_task_with_handoff",
        lambda *args, **kwargs: calls.append("finish") or {"status": "ok"},
    )
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "record_review_finding",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("approved review should not create finding")
        ),
    )

    def fake_copilot_review(**kwargs):  # noqa: ANN202
        calls.append("review")
        kwargs["invocation"].result_path.write_text(
            json.dumps(
                {
                    "task_status": "done",
                    "summary": "Copilot implemented under review.",
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
            command=kwargs["command"],
            metadata={
                "worker_review": {
                    "schema": "geond.worker_review.v1",
                    "stage": "implementation",
                    "decision": "approved",
                    "summary": "approved",
                    "findings": [],
                }
            },
        )

    monkeypatch.setattr(
        orchestrator.orchestrator_worker_review,
        "run_copilot_with_senior_review",
        fake_copilot_review,
    )

    payload = orchestrator.dispatch_spawn(
        object(),
        run_id="run-1",
        agent_name="copilot",
        execute=True,
        manifest_base_dir=tmp_path,
    )

    assert payload["execution_status"] == "completed"
    assert payload["worker_review"]["decision"] == "approved"
    assert calls == ["register", "claim", "review", "evidence", "finish"]


def test_copilot_execute_blocks_and_records_finding_when_review_blocks(
    monkeypatch,
    tmp_path: Path,
) -> None:
    patch_spawn_context(monkeypatch, tmp_path)
    findings: list[dict[str, object]] = []

    monkeypatch.setattr(
        orchestrator_spawn,
        "find_agent_binary",
        lambda agent_name: "/opt/homebrew/bin/gh" if agent_name == "copilot" else "/bin/codex",
    )
    monkeypatch.setattr(
        orchestrator_spawn,
        "prepare_git_worktree",
        lambda **kwargs: {
            "status": "ok",
            "code": None,
            "mode": "git_worktree",
            "source_workspace_path": kwargs["source_workspace_path"],
            "worktree_path": str(kwargs["worktree_path"]),
            "branch_name": kwargs["branch_name"],
            "created": kwargs["create"],
        },
    )
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "register_worker_session",
        lambda *args, **kwargs: {"status": "ok", "worker_session": {"worker_session_id": "w"}},
    )
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "claim_task",
        lambda *args, **kwargs: {"status": "ok", "lease": {"lease_id": "lease-1"}},
    )
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "record_command_evidence",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no evidence expected")),
    )
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "finish_task_with_handoff",
        lambda *args, **kwargs: {"status": "ok", "task": {"status": kwargs["task_status"]}},
    )

    def fake_record_finding(conn, run_id, summary, **kwargs):  # noqa: ANN001, ANN202
        findings.append({"run_id": run_id, "summary": summary, **kwargs})
        return {"status": "ok", "review_finding": {"review_finding_id": "finding-1"}}

    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "record_review_finding",
        fake_record_finding,
    )

    def fake_copilot_review(**kwargs):  # noqa: ANN202
        kwargs["invocation"].result_path.write_text(
            json.dumps(
                {
                    "task_status": "blocked",
                    "summary": "Senior review blocked Copilot output.",
                    "tested_commands": [],
                    "changed_files": [],
                    "risks": ["unsafe change"],
                    "next_action": "revise implementation",
                }
            ),
            encoding="utf-8",
        )
        return orchestrator_spawn.CodexRunResult(
            exit_code=0,
            timed_out=False,
            stdout="",
            stderr="",
            command=kwargs["command"],
            metadata={
                "worker_review": {
                    "schema": "geond.worker_review.v1",
                    "stage": "implementation",
                    "decision": "blocked",
                    "summary": "unsafe change",
                    "findings": ["unsafe change"],
                }
            },
        )

    monkeypatch.setattr(
        orchestrator.orchestrator_worker_review,
        "run_copilot_with_senior_review",
        fake_copilot_review,
    )

    payload = orchestrator.dispatch_spawn(
        object(),
        run_id="run-1",
        agent_name="copilot",
        execute=True,
        manifest_base_dir=tmp_path,
    )

    assert payload["execution_status"] == "blocked"
    assert payload["worker_result"]["task_status"] == "blocked"
    assert findings[0]["severity"] == "P1"
    assert findings[0]["task_id"] == "task-1"


def test_spawn_parallel_execute_reports_partial_success(monkeypatch, tmp_path: Path) -> None:
    patch_spawn_context(monkeypatch, tmp_path, task_count=2)
    monkeypatch.setattr(
        orchestrator_spawn,
        "find_agent_binary",
        lambda agent_name: f"/bin/{agent_name}",
    )
    evidence_calls: list[str] = []
    finish_statuses: list[str] = []

    def fake_register(conn, run_id, agent_name, **kwargs):  # noqa: ANN001, ANN202
        return {
            "status": "ok",
            "worker_session": {
                "worker_session_id": f"worker-{agent_name}",
                "run_id": run_id,
                "agent_name": agent_name,
            },
        }

    def fake_claim(conn, task_id, agent_name, **kwargs):  # noqa: ANN001, ANN202
        return {
            "status": "ok",
            "lease": {"lease_id": f"lease-{task_id}"},
            "task": {"task_id": task_id},
        }

    def fake_record(conn, run_id, command, **kwargs):  # noqa: ANN001, ANN202
        evidence_calls.append(kwargs["task_id"])
        return {
            "status": "ok",
            "command_evidence": {"command_evidence_id": f"evidence-{kwargs['task_id']}"},
        }

    def fake_finish(*args, **kwargs):  # noqa: ANN001, ANN202
        finish_statuses.append(kwargs["task_status"])
        return {"status": "ok", "handoff_id": f"handoff-{kwargs['task_status']}"}

    monkeypatch.setattr(orchestrator.orchestration_store, "register_worker_session", fake_register)
    monkeypatch.setattr(orchestrator.orchestration_store, "claim_task", fake_claim)
    monkeypatch.setattr(orchestrator.orchestration_store, "record_command_evidence", fake_record)
    monkeypatch.setattr(orchestrator.orchestration_store, "finish_task_with_handoff", fake_finish)

    def fake_runner(command, prompt, invocation, timeout_seconds):  # noqa: ANN001, ANN202
        if '"task_id": "task-2"' in prompt:
            return orchestrator_spawn.CodexRunResult(
                exit_code=1,
                timed_out=False,
                stdout="",
                stderr="failed task",
                command=command,
            )
        invocation.last_message_path.write_text(
            json.dumps(
                {
                    "task_status": "done",
                    "summary": "Completed first task.",
                    "tested_commands": [{"command": "uv run pytest", "exit_code": 0}],
                    "changed_files": [],
                    "risks": [],
                    "next_action": "continue",
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

    payload = orchestrator.dispatch_spawn(
        object(),
        run_id="run-1",
        execute=True,
        task_ids=["task-1", "task-2"],
        agent_names=["codex", "claude"],
        max_workers=2,
        manifest_base_dir=tmp_path,
        codex_runner=fake_runner,
    )

    assert payload["overall_execution_status"] == "partial"
    assert payload["completed_count"] == 1
    assert payload["failed_count"] == 1
    assert evidence_calls == ["task-1"]
    assert sorted(finish_statuses) == ["blocked", "done"]


def test_spawn_execute_blocks_on_codex_failure_without_evidence(
    monkeypatch,
    tmp_path: Path,
) -> None:
    patch_spawn_context(monkeypatch, tmp_path)
    calls: list[str] = []

    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "register_worker_session",
        lambda *args, **kwargs: {
            "status": "ok",
            "worker_session": {"worker_session_id": "worker-1"},
        },
    )
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "claim_task",
        lambda *args, **kwargs: {"status": "ok", "lease": {"lease_id": "lease-1"}},
    )
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "record_command_evidence",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no evidence expected")),
    )

    def fake_finish(*args, **kwargs):  # noqa: ANN001, ANN202
        calls.append("finish")
        assert kwargs["task_status"] == "blocked"
        return {"status": "ok", "handoff_id": "handoff-1"}

    monkeypatch.setattr(orchestrator.orchestration_store, "finish_task_with_handoff", fake_finish)

    def failing_runner(command, prompt, invocation, timeout_seconds):  # noqa: ANN001, ANN202
        return orchestrator_spawn.CodexRunResult(
            exit_code=1,
            timed_out=False,
            stdout="",
            stderr="boom",
            command=command,
        )

    payload = orchestrator.dispatch_spawn(
        object(),
        run_id="run-1",
        execute=True,
        manifest_base_dir=tmp_path,
        codex_runner=failing_runner,
    )

    assert payload["status"] == "ok"
    assert payload["execution_status"] == "blocked"
    assert payload["code"] == "CODEX_RUN_FAILED"
    assert calls == ["finish"]


def test_spawn_returns_clear_errors_for_missing_claude_or_unclaimable(
    monkeypatch,
    tmp_path: Path,
) -> None:
    patch_spawn_context(monkeypatch, tmp_path)
    monkeypatch.setattr(orchestrator_spawn, "find_agent_binary", lambda agent_name: None)

    missing = orchestrator.dispatch_spawn(
        object(),
        run_id="run-1",
        agent_name="claude",
        manifest_base_dir=tmp_path,
    )

    patch_spawn_context(monkeypatch, tmp_path, claimable=False)
    unclaimable = orchestrator.dispatch_spawn(object(), run_id="run-1", manifest_base_dir=tmp_path)

    assert missing["code"] == "CLAUDE_CLI_NOT_FOUND"
    assert unclaimable["code"] == "TASK_NOT_CLAIMABLE"


def test_claude_spawn_accepts_wrapper_json_result(monkeypatch, tmp_path: Path) -> None:
    patch_spawn_context(monkeypatch, tmp_path)
    calls: list[str] = []

    monkeypatch.setattr(
        orchestrator_spawn,
        "find_agent_binary",
        lambda agent_name: "/bin/claude" if agent_name == "claude" else "/bin/codex",
    )
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "register_worker_session",
        lambda *args, **kwargs: (
            calls.append("register")
            or {"status": "ok", "worker_session": {"worker_session_id": "worker-1"}}
        ),
    )
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "claim_task",
        lambda *args, **kwargs: (
            calls.append("claim") or {"status": "ok", "lease": {"lease_id": "lease-1"}}
        ),
    )
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "record_command_evidence",
        lambda *args, **kwargs: calls.append("evidence") or {"status": "ok"},
    )
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "finish_task_with_handoff",
        lambda *args, **kwargs: calls.append("finish") or {"status": "ok"},
    )

    worker_payload = {
        "task_status": "done",
        "summary": "Claude completed the task.",
        "tested_commands": [{"command": "uv run pytest", "exit_code": 0}],
        "changed_files": [],
        "risks": [],
        "next_action": "finalize",
    }

    def fake_runner(command, prompt, invocation, timeout_seconds):  # noqa: ANN001, ANN202
        invocation.last_message_path.write_text(
            json.dumps({"result": json.dumps(worker_payload)}),
            encoding="utf-8",
        )
        return orchestrator_spawn.CodexRunResult(
            exit_code=0,
            timed_out=False,
            stdout="",
            stderr="",
            command=command,
        )

    payload = orchestrator.dispatch_spawn(
        object(),
        run_id="run-1",
        agent_name="claude",
        execute=True,
        manifest_base_dir=tmp_path,
        codex_runner=fake_runner,
    )

    assert payload["status"] == "ok"
    assert payload["execution_status"] == "completed"
    assert payload["agent_name"] == "claude"
    assert calls == ["register", "claim", "evidence", "finish"]


def test_claude_command_runner_parses_fake_executable_wrapper_json(tmp_path: Path) -> None:
    worker_payload = {
        "task_status": "done",
        "summary": "Fake Claude completed the task.",
        "tested_commands": [],
        "changed_files": [],
        "risks": [],
        "next_action": "finalize",
    }
    fake_claude = tmp_path / "claude"
    fake_claude.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "sys.stdin.read()\n"
        f"print(json.dumps({{'result': {json.dumps(json.dumps(worker_payload))}}}))\n",
        encoding="utf-8",
    )
    fake_claude.chmod(0o755)

    invocation = orchestrator_spawn.new_invocation("run-1", tmp_path)
    command = orchestrator_spawn.build_agent_command(
        agent_name="claude",
        agent_bin=str(fake_claude),
        workspace_path=str(tmp_path),
        invocation=invocation,
        model="sonnet",
    )
    run_result = orchestrator_spawn.run_codex(
        command=command,
        prompt="do the task",
        invocation=invocation,
        timeout_seconds=10,
    )
    parsed = orchestrator_spawn.parse_worker_result(invocation)

    assert run_result.exit_code == 0
    assert "--max-turns" in command[-1]
    assert "--model sonnet" in command[-1]
    assert parsed["status"] == "ok"
    assert parsed["result"]["summary"] == "Fake Claude completed the task."


def test_spawn_preserves_failed_evidence_write_in_degraded_ledger(
    monkeypatch,
    tmp_path: Path,
) -> None:
    patch_spawn_context(monkeypatch, tmp_path)

    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "register_worker_session",
        lambda *args, **kwargs: {
            "status": "ok",
            "worker_session": {"worker_session_id": "worker-1"},
        },
    )
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "claim_task",
        lambda *args, **kwargs: {"status": "ok", "lease": {"lease_id": "lease-1"}},
    )
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "record_command_evidence",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("db down")),
    )
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "finish_task_with_handoff",
        lambda *args, **kwargs: {"status": "ok", "handoff_id": "handoff-1"},
    )

    def fake_runner(command, prompt, invocation, timeout_seconds):  # noqa: ANN001, ANN202
        invocation.last_message_path.write_text(
            json.dumps(
                {
                    "task_status": "done",
                    "summary": "Done but evidence write fails.",
                    "tested_commands": [{"command": "uv run pytest", "exit_code": 0}],
                    "changed_files": [],
                    "risks": [],
                    "next_action": "reconcile",
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

    payload = orchestrator.dispatch_spawn(
        object(),
        run_id="run-1",
        execute=True,
        manifest_base_dir=tmp_path,
        codex_runner=fake_runner,
    )

    pending = degraded_ledger.pending_events("run-1", tmp_path)
    assert payload["status"] == "degraded"
    assert payload["code"] == "DEGRADED_LEDGER_PENDING"
    assert pending[0]["event_type"] == "command_evidence"


def test_spawn_requested_task_with_active_lease_returns_conflict(
    monkeypatch,
    tmp_path: Path,
) -> None:
    status = spawn_status()
    status["claimable_tasks"] = []
    status["active_leases"] = [
        {
            "lease_id": "lease-1",
            "task_id": "task-1",
            "status": "active",
            "released_at": None,
        }
    ]
    monkeypatch.setattr(orchestrator, "get_status", lambda *args, **kwargs: status)
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "summarize_run",
        lambda conn, run_id: {"status": "ok", "markdown": "# Summary\n"},
    )

    payload = orchestrator.dispatch_spawn(
        object(),
        run_id="run-1",
        task_id="task-1",
        manifest_base_dir=tmp_path,
    )

    assert payload["code"] == "LEASE_CONFLICT"
    assert payload["execution_status"] == "blocked"
