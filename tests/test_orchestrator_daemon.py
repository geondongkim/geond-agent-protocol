from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from geond import orchestrator_daemon


def ok_budget(selected: int = 1) -> dict[str, object]:
    return {
        "schema": "geond.orchestrator_budget_report.v1",
        "status": "ok",
        "code": None,
        "decision": "allow",
        "forecast": {"selected_actions": selected},
        "blocking_reasons": [],
    }


def test_daemon_once_preview_does_not_execute_scheduler(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    calls: list[str] = []
    monkeypatch.setattr(
        orchestrator_daemon.orchestrator_budget,
        "build_budget_report",
        lambda *args, **kwargs: ok_budget(),
    )
    monkeypatch.setattr(
        orchestrator_daemon.orchestrator_scheduler,
        "drain_scheduler",
        lambda *args, **kwargs: (
            calls.append(str(kwargs.get("execute")))
            or {
                "schema": "geond.orchestrator_scheduler.v1",
                "status": "ok",
                "code": None,
                "execution_status": "preview",
                "selected_actions": [{"action_id": "a-1"}],
            }
        ),
    )

    payload = orchestrator_daemon.run_daemon_once(
        object(),
        workspace_id_or_uri="file:///repo",
        base_dir=tmp_path,
    )

    assert payload["schema"] == "geond.orchestrator_daemon.v1"
    assert payload["execution_status"] == "preview"
    assert calls == ["False"]
    assert not (tmp_path / orchestrator_daemon.workspace_key("file:///repo") / "daemon").exists()


def test_daemon_once_execute_locks_runs_scheduler_and_writes_trace(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    calls: list[str] = []
    monkeypatch.setattr(
        orchestrator_daemon.orchestrator_budget,
        "build_budget_report",
        lambda *args, **kwargs: ok_budget(),
    )
    monkeypatch.setattr(
        orchestrator_daemon.orchestrator_scheduler,
        "drain_scheduler",
        lambda *args, **kwargs: (
            calls.append(str(kwargs.get("execute")))
            or {
                "schema": "geond.orchestrator_scheduler.v1",
                "status": "ok",
                "code": None,
                "execution_status": "completed",
                "selected_actions": [{"action_id": "a-1"}],
            }
        ),
    )

    payload = orchestrator_daemon.run_daemon_once(
        object(),
        workspace_id_or_uri="file:///repo",
        execute=True,
        base_dir=tmp_path,
    )

    assert calls == ["True"]
    assert payload["status"] == "ok"
    assert Path(payload["bundle"]["trace_path"]).exists()
    assert orchestrator_daemon.read_lock("file:///repo", base_dir=tmp_path)["status"] == "none"


def test_daemon_active_lock_blocks_and_expired_lock_reclaims(tmp_path: Path) -> None:
    first = orchestrator_daemon.acquire_lock(
        "file:///repo",
        daemon_id="daemon-1",
        base_dir=tmp_path,
        ttl_seconds=120,
    )
    blocked = orchestrator_daemon.acquire_lock(
        "file:///repo",
        daemon_id="daemon-2",
        base_dir=tmp_path,
        ttl_seconds=120,
    )
    assert first["status"] == "ok"
    assert blocked["code"] == "DAEMON_LOCK_HELD"

    lock_path = Path(first["lock_path"])
    expired = dict(first)
    expired["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    lock_path.write_text(
        orchestrator_daemon.json.dumps(expired, ensure_ascii=False),
        encoding="utf-8",
    )
    reclaimed = orchestrator_daemon.acquire_lock(
        "file:///repo",
        daemon_id="daemon-3",
        base_dir=tmp_path,
        ttl_seconds=120,
    )
    assert reclaimed["status"] == "ok"
    assert reclaimed["reclaimed"] is True


def test_daemon_loop_rechecks_each_cycle_and_stops(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    drain_calls = 0
    monkeypatch.setattr(
        orchestrator_daemon.orchestrator_budget,
        "build_budget_report",
        lambda *args, **kwargs: ok_budget(),
    )

    def fake_drain(*args, **kwargs):  # noqa: ANN001, ANN202
        nonlocal drain_calls
        drain_calls += 1
        return {
            "schema": "geond.orchestrator_scheduler.v1",
            "status": "ok",
            "code": None,
            "execution_status": "completed",
            "selected_actions": [{"action_id": "a-1"}] if drain_calls == 1 else [],
        }

    monkeypatch.setattr(orchestrator_daemon.orchestrator_scheduler, "drain_scheduler", fake_drain)

    payload = orchestrator_daemon.run_daemon_loop(
        object(),
        workspace_id_or_uri="file:///repo",
        execute=True,
        max_cycles=2,
        interval_seconds=0,
        base_dir=tmp_path,
        sleep_fn=lambda seconds: None,
    )

    assert drain_calls == 2
    assert payload["code"] == "NO_APPROVED_ACTION"
    assert len(payload["cycles"]) == 2
