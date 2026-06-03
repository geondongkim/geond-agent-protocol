from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from geond.config import get_settings
from geond.db import connect, run_schema_file
from geond.mcp_server import server as mcp_server
from geond.storage import orchestration

SCHEMA = Path(__file__).parents[1] / "schemas" / "001_initial.sql"
ORCHESTRATION_SCHEMA = Path(__file__).parents[1] / "schemas" / "007_orchestration.sql"
TASK_GRAPH_SCHEMA = Path(__file__).parents[1] / "schemas" / "008_orchestration_task_graph.sql"


def _connect_with_schema() -> psycopg.Connection:
    settings = get_settings()
    try:
        conn = connect(settings)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres integration database is not available: {exc}")
    try:
        run_schema_file(conn, SCHEMA)
        run_schema_file(conn, ORCHESTRATION_SCHEMA)
        run_schema_file(conn, TASK_GRAPH_SCHEMA)
    except psycopg.Error as exc:
        pytest.skip(f"Postgres integration schema is not available: {exc}")
    return conn


def test_orchestration_goal_run_task_worker_lifecycle() -> None:
    workspace_uri = f"file:///tmp/geond-orchestration-storage-{uuid4()}"
    conn = _connect_with_schema()
    with conn:
        goal = orchestration.create_goal(
            conn,
            workspace_uri,
            "Prepare MCP orchestration",
            idempotency_key="goal-key",
        )
        replay = orchestration.create_goal(
            conn,
            workspace_uri,
            "Prepare MCP orchestration",
            idempotency_key="goal-key",
        )
        conflict = orchestration.create_goal(
            conn,
            workspace_uri,
            "Different payload",
            idempotency_key="goal-key",
        )
        assert goal["status"] == "ok"
        assert replay["goal"]["goal_id"] == goal["goal"]["goal_id"]
        assert replay["idempotent_replay"] is True
        assert conflict["code"] == "IDEMPOTENCY_CONFLICT"

        run = orchestration.create_run(
            conn,
            workspace_uri,
            "Run worker claim test",
            goal_id=goal["goal"]["goal_id"],
            risk_level="medium",
        )
        task = orchestration.create_task(conn, run["run"]["run_id"], "Implement storage")
        worker = orchestration.register_worker_session(
            conn,
            run["run"]["run_id"],
            agent_name="codex",
        )
        claim = orchestration.claim_task(
            conn,
            task["task"]["task_id"],
            agent_name="codex",
            worker_session_id=worker["worker_session"]["worker_session_id"],
            idempotency_key="claim-key",
        )
        replay_claim = orchestration.claim_task(
            conn,
            task["task"]["task_id"],
            agent_name="codex",
            worker_session_id=worker["worker_session"]["worker_session_id"],
            idempotency_key="claim-key",
        )
        second_claim = orchestration.claim_task(
            conn,
            task["task"]["task_id"],
            agent_name="claude",
        )
        assert claim["status"] == "ok"
        assert replay_claim["lease"]["lease_id"] == claim["lease"]["lease_id"]
        assert replay_claim["idempotent_replay"] is True
        assert second_claim["code"] == "LEASE_CONFLICT"

        renewed = orchestration.renew_task_lease(
            conn,
            claim["lease"]["lease_id"],
            worker_session_id=worker["worker_session"]["worker_session_id"],
        )
        assert renewed["lease"]["status"] == "renewed"

        finished = orchestration.finish_task_with_handoff(
            conn,
            claim["lease"]["lease_id"],
            summary="Storage lifecycle finished",
            tested_commands=["uv run pytest tests/test_orchestration_storage.py"],
        )
        assert finished["task"]["status"] == "done"
        assert finished["handoff_id"]

        not_ready = orchestration.get_readiness_report(conn, run["run"]["run_id"])
        assert not_ready["status"] == "not_ready"
        assert "no command evidence" in not_ready["blocking_reasons"]

        evidence = orchestration.record_command_evidence(
            conn,
            run["run"]["run_id"],
            command="uv run pytest tests/test_orchestration_storage.py",
            task_id=task["task"]["task_id"],
            exit_code=0,
        )
        assert evidence["command_evidence"]["status"] == "passed"
        ready = orchestration.get_readiness_report(conn, run["run"]["run_id"])
        assert ready["status"] == "ready"

        mcp_run = mcp_server.get_run(run["run"]["run_id"])
        run_resource = mcp_server.run_resource(run["run"]["run_id"])
        readiness_resource = mcp_server.run_readiness_resource(run["run"]["run_id"])
        assert mcp_run["tasks"][0]["task_id"] == task["task"]["task_id"]
        assert run_resource["tasks"][0]["task_id"] == task["task"]["task_id"]
        assert readiness_resource["schema"] == "geond.readiness_report.v1"

        finding = orchestration.record_review_finding(
            conn,
            run["run"]["run_id"],
            summary="P1 blocker",
            severity="P1",
        )
        assert finding["finding"]["severity"] == "P1"
        blocked = orchestration.get_readiness_report(conn, run["run"]["run_id"])
        assert blocked["status"] == "not_ready"
        assert blocked["open_findings"][0]["severity"] == "P1"

        resolved_finding = orchestration.resolve_review_finding(
            conn,
            finding["finding"]["finding_id"],
            status="fixed",
            reason="Added validation evidence",
            resolved_by="codex",
        )
        assert resolved_finding["finding"]["status"] == "fixed"
        assert resolved_finding["finding"]["resolved_at"]
        assert orchestration.get_readiness_report(conn, run["run"]["run_id"])["status"] == "ready"

        decision = orchestration.record_decision(
            conn,
            run["run"]["run_id"],
            decision="Proceed with MCP-first foundation",
            evidence_refs=[
                {
                    "type": "command_evidence",
                    "id": evidence["command_evidence"]["command_evidence_id"],
                }
            ],
        )
        assert decision["decision"]["decision_id"]

        package = orchestration.get_run_handoff_package(conn, run["run"]["run_id"])
        summary = orchestration.summarize_run(conn, run["run"]["run_id"])
        assert package["schema"] == "geond.run_handoff_package.v1"
        assert package["decisions"][0]["decision"] == "Proceed with MCP-first foundation"
        assert summary["schema"] == "geond.run_summary.v1"
        assert "Run worker claim test" in summary["markdown"]
        assert "uv run pytest tests/test_orchestration_storage.py" in summary["markdown"]

        mcp_package = mcp_server.get_run_handoff_package(run["run"]["run_id"])
        mcp_summary = mcp_server.summarize_run(run["run"]["run_id"])
        assert mcp_package["schema"] == "geond.run_handoff_package.v1"
        assert mcp_summary["schema"] == "geond.run_summary.v1"

        with conn.cursor() as cur:
            cur.execute("DELETE FROM workspaces WHERE root_uri = %s", (workspace_uri,))
        conn.commit()


def test_high_risk_run_requires_pending_approval_resolution() -> None:
    workspace_uri = f"file:///tmp/geond-orchestration-approval-{uuid4()}"
    conn = _connect_with_schema()
    with conn:
        run = orchestration.create_run(conn, workspace_uri, "High risk run", risk_level="high")
        task = orchestration.create_task(conn, run["run"]["run_id"], "Risky task")
        claim = orchestration.claim_task(conn, task["task"]["task_id"], agent_name="codex")
        orchestration.finish_task_with_handoff(conn, claim["lease"]["lease_id"], "Done")
        orchestration.record_command_evidence(
            conn,
            run["run"]["run_id"],
            "uv run pytest",
            exit_code=0,
        )
        approval = orchestration.request_approval(
            conn,
            run["run"]["run_id"],
            reason="Production-impacting action",
        )
        pending = orchestration.get_readiness_report(conn, run["run"]["run_id"])
        assert pending["status"] == "needs_human_approval"

        resolved = orchestration.resolve_approval(
            conn,
            approval["approval"]["approval_id"],
            status="approved",
            resolved_by="human",
        )
        assert resolved["approval"]["status"] == "approved"
        ready = orchestration.get_readiness_report(conn, run["run"]["run_id"])
        assert ready["status"] == "ready"

        with conn.cursor() as cur:
            cur.execute("DELETE FROM workspaces WHERE root_uri = %s", (workspace_uri,))
        conn.commit()


def test_task_graph_dependencies_gate_claimable_tasks() -> None:
    workspace_uri = f"file:///tmp/geond-orchestration-task-graph-{uuid4()}"
    conn = _connect_with_schema()
    with conn:
        run = orchestration.create_run(conn, workspace_uri, "Task graph run")
        graph = orchestration.create_task_graph(
            conn,
            run["run"]["run_id"],
            [
                {"key": "repro", "title": "Reproduce issue", "priority": 100},
                {
                    "key": "fix",
                    "title": "Implement fix",
                    "priority": 50,
                    "depends_on": ["repro"],
                },
            ],
        )
        assert graph["status"] == "ok"
        by_title = {task["title"]: task for task in graph["tasks"]}
        claimable = orchestration.get_claimable_tasks(conn, run_id=run["run"]["run_id"])
        blocked = orchestration.get_blocked_task_reasons(conn, run["run"]["run_id"])
        assert [task["title"] for task in claimable["tasks"]] == ["Reproduce issue"]
        assert blocked["blocked_tasks"][0]["title"] == "Implement fix"

        worker = orchestration.register_worker_session(conn, run["run"]["run_id"], "codex")
        claim = orchestration.claim_task(
            conn,
            by_title["Reproduce issue"]["task_id"],
            "codex",
            worker_session_id=worker["worker_session"]["worker_session_id"],
        )
        orchestration.finish_task_with_handoff(conn, claim["lease"]["lease_id"], "Repro done")
        claimable_after_repro = orchestration.get_claimable_tasks(conn, run_id=run["run"]["run_id"])
        assert [task["title"] for task in claimable_after_repro["tasks"]] == ["Implement fix"]

        claim_fix = orchestration.claim_task(
            conn,
            by_title["Implement fix"]["task_id"],
            "codex",
            worker_session_id=worker["worker_session"]["worker_session_id"],
        )
        orchestration.finish_task_with_handoff(conn, claim_fix["lease"]["lease_id"], "Fix done")
        orchestration.record_command_evidence(
            conn,
            run["run"]["run_id"],
            "uv run pytest",
            exit_code=0,
        )
        assert orchestration.get_readiness_report(conn, run["run"]["run_id"])["status"] == "ready"

        with conn.cursor() as cur:
            cur.execute("DELETE FROM workspaces WHERE root_uri = %s", (workspace_uri,))
        conn.commit()
