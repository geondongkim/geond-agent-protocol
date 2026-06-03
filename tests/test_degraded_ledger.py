from __future__ import annotations

from geond import degraded_ledger


def test_degraded_ledger_append_read_and_reconcile_dry_run(tmp_path) -> None:
    event = degraded_ledger.append_event(
        run_id="run-1",
        base_dir=tmp_path,
        event_type="command_evidence",
        payload={
            "run_id": "run-1",
            "command": "uv run pytest",
            "task_id": "task-1",
        },
        idempotency_key="ledger-key-1",
        db_status="pending",
        source_command="geond-orchestrator dispatch --mode spawn --execute",
    )

    events = degraded_ledger.read_events("run-1", tmp_path)
    summary = degraded_ledger.ledger_summary("run-1", tmp_path)
    dry_run = degraded_ledger.reconcile(object(), run_id="run-1", base_dir=tmp_path, dry_run=True)

    assert events[0]["event_id"] == event["event_id"]
    assert summary["pending_count"] == 1
    assert dry_run["results"] == [{"event_id": event["event_id"], "action": "would_apply"}]


def test_degraded_ledger_reconcile_applies_pending_events_once(tmp_path, monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    degraded_ledger.append_event(
        run_id="run-1",
        base_dir=tmp_path,
        event_type="command_evidence",
        payload={
            "run_id": "run-1",
            "command": "uv run pytest",
            "task_id": "task-1",
            "worker_session_id": "worker-1",
            "purpose": "validation",
            "metadata": {"source": "test"},
        },
        idempotency_key="ledger-key-1",
        db_status="pending",
        source_command="spawn",
    )

    def fake_record(conn, run_id, command, **kwargs):  # noqa: ANN001, ANN202
        calls.append({"run_id": run_id, "command": command, **kwargs})
        return {"status": "ok", "command_evidence": {"command_evidence_id": "cmd-1"}}

    monkeypatch.setattr(
        degraded_ledger.orchestration_store,
        "record_command_evidence",
        fake_record,
    )

    first = degraded_ledger.reconcile(object(), run_id="run-1", base_dir=tmp_path)
    second = degraded_ledger.reconcile(object(), run_id="run-1", base_dir=tmp_path)

    assert first["applied_count"] == 1
    assert second["pending_count"] == 0
    assert len(calls) == 1
    assert calls[0]["idempotency_key"] == "ledger-key-1"
