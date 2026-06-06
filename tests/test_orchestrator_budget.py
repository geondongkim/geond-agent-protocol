from __future__ import annotations

from pathlib import Path

from geond import orchestrator_budget


def test_budget_blocks_when_usage_data_missing_for_token_budget(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        orchestrator_budget.orchestrator_action_queue,
        "list_action_queue",
        lambda *args, **kwargs: {
            "schema": "geond.orchestrator_action_queue.v1",
            "status": "ok",
            "items": [
                {
                    "run_id": "run-1",
                    "action_id": "spawn-1",
                    "action_type": "dispatch_spawn",
                    "status": "approved",
                    "queued_at": "2026-06-07T00:00:00+00:00",
                }
            ],
        },
    )
    monkeypatch.setattr(
        orchestrator_budget,
        "usage_totals",
        lambda *args, **kwargs: {
            "data_available": False,
            "event_count": 0,
            "total_tokens": 0,
            "estimated_cost_usd": "0",
        },
    )

    payload = orchestrator_budget.build_budget_report(
        object(),
        workspace_id_or_uri="file:///repo",
        budget_tokens=100,
        estimate_spawn_tokens=25,
        base_dir=tmp_path,
    )

    assert payload["schema"] == "geond.orchestrator_budget_report.v1"
    assert payload["decision"] == "blocked"
    assert payload["code"] == "ORCHESTRATOR_BUDGET_EXCEEDED"
    assert payload["blocking_reasons"][0]["code"] == "USAGE_DATA_UNAVAILABLE"


def test_budget_allows_usage_plus_forecast_within_limits(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        orchestrator_budget,
        "usage_totals",
        lambda *args, **kwargs: {
            "data_available": True,
            "event_count": 2,
            "total_tokens": 50,
            "estimated_cost_usd": "0.20",
        },
    )

    payload = orchestrator_budget.build_budget_report(
        object(),
        workspace_id_or_uri="file:///repo",
        selected_actions=[
            {"run_id": "run-1", "action_id": "spawn-1", "action_type": "dispatch_spawn"}
        ],
        budget_tokens=100,
        budget_cost_usd="1.00",
        estimate_spawn_tokens=25,
        estimate_spawn_cost_usd="0.10",
        base_dir=tmp_path,
    )

    assert payload["status"] == "ok"
    assert payload["decision"] == "allow"
    assert payload["forecast"]["projected_tokens"] == 75
    assert payload["forecast"]["projected_cost_usd"] == "0.30"
    assert payload["remaining"]["tokens"] == 25
    assert payload["remaining"]["cost_usd"] == "0.70"


def test_budget_blocks_action_spawn_token_and_cost_limits(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        orchestrator_budget,
        "usage_totals",
        lambda *args, **kwargs: {
            "data_available": True,
            "event_count": 1,
            "total_tokens": 90,
            "estimated_cost_usd": "0.90",
        },
    )

    payload = orchestrator_budget.build_budget_report(
        object(),
        workspace_id_or_uri="file:///repo",
        selected_actions=[
            {"run_id": "run-1", "action_id": "spawn-1", "action_type": "dispatch_spawn"},
            {"run_id": "run-2", "action_id": "spawn-2", "action_type": "dispatch_spawn"},
        ],
        budget_actions=1,
        budget_spawn_actions=1,
        budget_tokens=100,
        budget_cost_usd="1.00",
        estimate_spawn_tokens=10,
        estimate_spawn_cost_usd="0.10",
        base_dir=tmp_path,
    )

    assert payload["decision"] == "blocked"
    assert {reason["code"] for reason in payload["blocking_reasons"]} == {
        "ACTION_BUDGET_EXCEEDED",
        "SPAWN_ACTION_BUDGET_EXCEEDED",
        "TOKEN_BUDGET_EXCEEDED",
        "COST_BUDGET_EXCEEDED",
    }
