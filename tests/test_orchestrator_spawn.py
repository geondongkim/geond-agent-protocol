from __future__ import annotations

import json
from pathlib import Path

from geond import degraded_ledger, orchestrator, orchestrator_spawn


def spawn_status(task_id: str = "task-1") -> dict[str, object]:
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
        "claimable_tasks": [
            {
                "task_id": task_id,
                "title": "Implement task",
                "description": "Do the work",
                "status": "ready",
            }
        ],
        "open_findings": [],
        "pending_approvals": [],
        "latest_decisions": [],
    }


def patch_spawn_context(monkeypatch, tmp_path: Path, *, claimable: bool = True) -> None:
    status = spawn_status()
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


def test_spawn_execute_records_evidence_then_finishes(monkeypatch, tmp_path: Path) -> None:
    patch_spawn_context(monkeypatch, tmp_path)
    calls: list[str] = []

    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "register_worker_session",
        lambda *args, **kwargs: calls.append("register")
        or {
            "status": "ok",
            "worker_session": {
                "worker_session_id": "worker-1",
                "run_id": "run-1",
                "agent_name": "codex",
            },
        },
    )
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "claim_task",
        lambda *args, **kwargs: calls.append("claim")
        or {
            "status": "ok",
            "lease": {"lease_id": "lease-1"},
            "task": {"task_id": "task-1"},
        },
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
        lambda *args, **kwargs: calls.append("register")
        or {"status": "ok", "worker_session": {"worker_session_id": "worker-1"}},
    )
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "claim_task",
        lambda *args, **kwargs: calls.append("claim")
        or {"status": "ok", "lease": {"lease_id": "lease-1"}},
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
