from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from geond.cli_tasks import finish_task, start_task
from geond.code_graph.python_indexer import index_python_file
from geond.config import get_settings
from geond.db import connect, run_schema_file
from geond.storage.benchmark import save_benchmark_run
from geond.storage.code_graph import store_code_index, store_lsp_references
from geond.storage.context_review import review_workspace_context
from geond.storage.repository import (
    cleanup_expired_reservations_for_workspace,
    close_handoff_summary,
    get_workspace_coordination_policy,
    list_active_file_reservations,
    list_active_symbol_reservations,
    list_handoff_summaries,
    list_reservation_events,
    record_agent_action,
    record_changeset,
    record_handoff_summary,
    release_reservation,
    release_symbol_reservation,
    renew_reservation,
    renew_symbol_reservation,
    reserve_files,
    reserve_symbols,
    set_workspace_coordination_policy,
    upsert_workspace,
)
from geond.storage.resources import (
    get_symbol_resource,
    get_workspace_handoffs,
    get_workspace_lineage,
    get_workspace_reservations,
    get_workspace_timeline,
)

SCHEMA = Path(__file__).parents[1] / "schemas" / "001_initial.sql"


def test_symbol_resource_and_file_reservations(tmp_path: Path) -> None:
    settings = get_settings()
    workspace_uri = f"file:///tmp/geond-resource-test-{uuid4()}"
    source = tmp_path / "service.py"
    source.write_text(
        """
def build_answer(prompt):
    return prompt.strip()

def use_answer(prompt):
    return build_answer(prompt)
""".strip(),
        encoding="utf-8",
    )

    try:
        conn = connect(settings)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres integration database is not available: {exc}")

    with conn:
        try:
            run_schema_file(conn, SCHEMA)
        except psycopg.Error as exc:
            pytest.skip(f"Postgres integration schema is not available: {exc}")

        workspace_id = upsert_workspace(
            conn,
            root_uri=workspace_uri,
            name="resource-fixture",
            metadata={"source": "pytest"},
        )
        try:
            store_code_index(conn, workspace_id, [index_python_file(source, tmp_path)])
            lsp_references = store_lsp_references(
                conn,
                workspace_id,
                [
                    {
                        "target_qualified_name": "service.build_answer",
                        "source_qualified_name": "service.use_answer",
                        "reference": {"file_path": "service.py", "start_line": 4},
                        "provider": "pytest-lsp",
                    }
                ],
            )
            symbol = get_symbol_resource(conn, "build_answer")
            first = reserve_files(
                conn,
                workspace_id,
                agent_name="agent-a",
                file_paths=["service.py"],
                purpose="edit function",
                ttl_minutes=30,
            )
            second = reserve_files(
                conn,
                workspace_id,
                agent_name="agent-b",
                file_paths=["service.py"],
                purpose="parallel edit",
                ttl_minutes=30,
            )
            expired_file = reserve_files(
                conn,
                workspace_id,
                agent_name="agent-old",
                file_paths=["old.py"],
                purpose="expired edit",
                ttl_minutes=-1,
            )
            symbol_first = reserve_symbols(
                conn,
                workspace_id,
                agent_name="agent-a",
                symbols=["build_answer"],
                purpose="rename function",
                ttl_minutes=30,
            )
            symbol_second = reserve_symbols(
                conn,
                workspace_id,
                agent_name="agent-b",
                symbols=["service.build_answer"],
                purpose="edit function body",
                ttl_minutes=30,
            )
            expired_symbol = reserve_symbols(
                conn,
                workspace_id,
                agent_name="agent-old",
                symbols=["expired_symbol"],
                purpose="expired symbol edit",
                ttl_minutes=-1,
            )
            cleaned = cleanup_expired_reservations_for_workspace(conn, workspace_id)
            active = list_active_file_reservations(conn, workspace_id)
            active_symbols = list_active_symbol_reservations(conn, workspace_id)
            renewed = renew_reservation(
                conn,
                workspace_id,
                reservation_id=first["reservation_ids"][0],
                agent_name="agent-a",
                ttl_minutes=45,
            )
            renewed_symbol = renew_symbol_reservation(
                conn,
                workspace_id,
                reservation_id=symbol_first["reservation_ids"][0],
                agent_name="agent-a",
                ttl_minutes=45,
            )
            record_agent_action(
                conn,
                workspace_id=workspace_id,
                agent_name="agent-a",
                action_type="index",
                summary="Indexed service.py",
            )
            handoff_id = record_handoff_summary(
                conn,
                workspace_id=workspace_id,
                from_agent_name="agent-a",
                to_agent_name="agent-b",
                summary="build_answer is indexed and reserved for a rename check.",
                next_steps=["Review symbol conflict before editing build_answer."],
                next_action="Confirm rename plan with agent-b.",
                blocked_on=[],
                tested_commands=["uv run pytest tests/test_resources_and_coordination.py"],
                remaining_risks=["Symbol conflict may need an override reason."],
            )
            changeset = record_changeset(
                conn,
                workspace_id=workspace_id,
                files=[{"file_path": "service.py", "status": "modified"}],
                branch="main",
                intent="rename preparation",
                summary="Documented symbol coordination state.",
            )
            benchmark_id = save_benchmark_run(
                conn,
                result={"mode": "keyword", "repeat": 1, "queries": []},
                label="coordination smoke",
                workspace_uri=workspace_id,
            )
            handoffs = list_handoff_summaries(conn, workspace_id, status="open")
            reservation_resource = get_workspace_reservations(conn, workspace_id)
            handoff_resource = get_workspace_handoffs(conn, workspace_id)
            timeline = get_workspace_timeline(conn, workspace_id)
            lineage = get_workspace_lineage(conn, workspace_id)
            context_review = review_workspace_context(
                conn,
                workspace_id,
                intent="rename build_answer after checking service.py context",
                file_paths=["service.py"],
                symbols=["build_answer"],
                agent_name="agent-b",
            )
            released = release_reservation(
                conn,
                workspace_id,
                reservation_id=first["reservation_ids"][0],
            )
            released_symbol = release_symbol_reservation(
                conn,
                workspace_id,
                reservation_id=symbol_first["reservation_ids"][0],
            )
            reservation_events = list_reservation_events(conn, workspace_id)
            closed_handoff = close_handoff_summary(conn, handoff_id)

            assert any(
                entity["qualified_name"] == "service.build_answer" for entity in symbol["entities"]
            )
            build_answer_entity = next(
                entity
                for entity in symbol["entities"]
                if entity["qualified_name"] == "service.build_answer"
            )
            assert lsp_references["references"] == 1
            assert build_answer_entity["references"][0]["qualified_name"] == "service.use_answer"
            assert build_answer_entity["references"][0]["edge"]["metadata"]["source"] == "lsp"
            assert first["conflicts"] == []
            assert second["conflicts"][0]["agent_name"] == "agent-a"
            assert symbol_first["conflicts"] == []
            assert symbol_first["resolved_symbols"]["build_answer"]["file_path"] == "service.py"
            assert symbol_second["conflicts"][0]["agent_name"] == "agent-a"
            assert expired_file["reservation_ids"]
            assert expired_symbol["reservation_ids"]
            assert cleaned["symbol_reservations"] >= 1
            assert len(active) == 2
            assert all(item["file_path"] != "old.py" for item in active)
            assert len(active_symbols) == 2
            assert renewed == 1
            assert renewed_symbol == 1
            assert handoffs[0]["handoff_id"] == handoff_id
            assert handoffs[0]["next_steps"][-1] == "Confirm rename plan with agent-b."
            assert handoffs[0]["metadata"]["handoff_template"]["tested_commands"] == [
                "uv run pytest tests/test_resources_and_coordination.py"
            ]
            assert handoffs[0]["metadata"]["handoff_template"]["remaining_risks"] == [
                "Symbol conflict may need an override reason."
            ]
            lineage_kinds = {node["kind"] for node in lineage["nodes"]}
            assert {
                "agent",
                "agent_action",
                "handoff_summary",
                "changeset",
                "benchmark_run",
            } <= lineage_kinds
            assert any(node["raw_id"] == changeset["changeset_id"] for node in lineage["nodes"])
            assert any(node["raw_id"] == benchmark_id for node in lineage["nodes"])
            assert any(edge["kind"] == "handoff_from" for edge in lineage["edges"])
            assert any(edge["kind"] == "precedes" for edge in lineage["edges"])
            assert context_review["assessment"]["status"] == "advisory_conflicts"
            assert context_review["assessment"]["external_conflict_count"] >= 1
            assert context_review["matches"]
            assert any(
                "coordination decision" in item for item in context_review["recommendations"]
            )
            assert reservation_resource["symbol_reservations"]
            assert handoff_resource["handoffs"][0]["summary"].startswith("build_answer")
            assert any(event["kind"] == "agent_action" for event in timeline["events"])
            assert any(event["kind"] == "file_reservation" for event in timeline["events"])
            assert any(event["kind"] == "symbol_reservation" for event in timeline["events"])
            assert any(event["kind"] == "reservation_event" for event in timeline["events"])
            assert any(event["kind"] == "handoff_summary" for event in timeline["events"])
            assert released == 1
            assert released_symbol == 1
            assert reservation_resource["recent_events"]
            assert {event["action"] for event in reservation_events} >= {
                "created",
                "renewed",
                "released",
                "expired",
            }
            assert closed_handoff == 1
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM workspaces WHERE id = %s", (workspace_id,))
            conn.commit()


def test_reservation_conflict_policy_blocks_or_requires_override(tmp_path: Path) -> None:
    settings = get_settings()
    workspace_uri = f"file:///tmp/geond-policy-test-{uuid4()}"
    source = tmp_path / "service.py"
    source.write_text(
        """
def build_answer(prompt):
    return prompt.strip()
""".strip(),
        encoding="utf-8",
    )

    try:
        conn = connect(settings)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres integration database is not available: {exc}")

    with conn:
        try:
            run_schema_file(conn, SCHEMA)
        except psycopg.Error as exc:
            pytest.skip(f"Postgres integration schema is not available: {exc}")

        workspace_id = upsert_workspace(
            conn,
            root_uri=workspace_uri,
            name="policy-fixture",
            metadata={"source": "pytest"},
        )
        try:
            store_code_index(conn, workspace_id, [index_python_file(source, tmp_path)])
            default_policy = get_workspace_coordination_policy(conn, workspace_id)
            first = reserve_files(
                conn,
                workspace_id,
                agent_name="agent-a",
                file_paths=["service.py"],
                purpose="first edit",
            )
            strict_policy = set_workspace_coordination_policy(
                conn,
                workspace_id,
                reservation_conflict_policy="strict",
            )
            strict_blocked = reserve_files(
                conn,
                workspace_id,
                agent_name="agent-b",
                file_paths=["service.py"],
                purpose="parallel edit",
            )
            override_policy = set_workspace_coordination_policy(
                conn,
                workspace_id,
                reservation_conflict_policy="override-with-reason",
            )
            symbol_first = reserve_symbols(
                conn,
                workspace_id,
                agent_name="agent-a",
                symbols=["build_answer"],
                purpose="symbol owner",
            )
            missing_reason = reserve_symbols(
                conn,
                workspace_id,
                agent_name="agent-b",
                symbols=["build_answer"],
                purpose="parallel symbol edit",
            )
            symbol_override = reserve_symbols(
                conn,
                workspace_id,
                agent_name="agent-b",
                symbols=["build_answer"],
                purpose="pairing on symbol",
                override_reason="pairing agreed in handoff",
            )
            file_override = reserve_files(
                conn,
                workspace_id,
                agent_name="agent-c",
                file_paths=["service.py"],
                purpose="override edit",
                override_reason="urgent production fix",
            )
            events = list_reservation_events(conn, workspace_id)

            assert default_policy["reservation_conflict_policy"] == "advisory"
            assert first["reservation_ids"]
            assert strict_policy["reservation_conflict_policy"] == "strict"
            assert strict_blocked["blocked"] is True
            assert strict_blocked["reservation_ids"] == []
            assert strict_blocked["block_reason"] == "strict_conflict_policy"
            assert override_policy["reservation_conflict_policy"] == "override-with-reason"
            assert symbol_first["reservation_ids"]
            assert missing_reason["blocked"] is True
            assert missing_reason["block_reason"] == "override_reason_required"
            assert symbol_override["blocked"] is False
            assert symbol_override["conflicts"][0]["agent_name"] == "agent-a"
            assert file_override["blocked"] is False
            assert file_override["conflicts"][0]["agent_name"] == "agent-a"
            assert any(
                event["metadata"].get("override_reason") == "urgent production fix"
                for event in events
            )
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM workspaces WHERE id = %s", (workspace_id,))
            conn.commit()


def test_agent_actions_and_changesets_can_link_to_sessions() -> None:
    settings = get_settings()
    workspace_uri = f"file:///tmp/geond-session-link-test-{uuid4()}"

    try:
        conn = connect(settings)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres integration database is not available: {exc}")

    with conn:
        try:
            run_schema_file(conn, SCHEMA)
        except psycopg.Error as exc:
            pytest.skip(f"Postgres integration schema is not available: {exc}")

        workspace_id = upsert_workspace(
            conn,
            root_uri=workspace_uri,
            name="session-link-fixture",
            metadata={"source": "pytest"},
        )
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO sessions (workspace_id, source, external_id, title, metadata)
                    VALUES (%s, %s, %s, %s, '{}'::jsonb)
                    RETURNING id::text
                    """,
                    (
                        workspace_id,
                        "codex",
                        "session-link-external",
                        "Session linkage fixture",
                    ),
                )
                session_id = cur.fetchone()[0]
            conn.commit()

            action_id = record_agent_action(
                conn,
                workspace_id=workspace_id,
                agent_name="agent-a",
                action_type="task_start",
                summary="Start linked work",
                session_external_id="session-link-external",
            )
            changeset = record_changeset(
                conn,
                workspace_id=workspace_id,
                files=[{"file_path": "service.py", "status": "modified"}],
                summary="Linked change evidence",
                session_id=session_id,
            )

            with conn.cursor() as cur:
                cur.execute(
                    "SELECT session_id::text FROM agent_actions WHERE id = %s",
                    (action_id,),
                )
                action_session_id = cur.fetchone()[0]
                cur.execute(
                    "SELECT session_id::text FROM changesets WHERE id = %s",
                    (changeset["changeset_id"],),
                )
                changeset_session_id = cur.fetchone()[0]

            lineage = get_workspace_lineage(conn, workspace_id)
            assert action_session_id == session_id
            assert changeset_session_id == session_id
            assert any(
                edge["kind"] == "session_contains"
                and edge["source"] == f"session:{session_id}"
                and edge["target"] == f"agent_action:{action_id}"
                for edge in lineage["edges"]
            )
            assert any(
                edge["kind"] == "session_contains"
                and edge["source"] == f"session:{session_id}"
                and edge["target"] == f"changeset:{changeset['changeset_id']}"
                for edge in lineage["edges"]
            )
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM workspaces WHERE id = %s", (workspace_id,))
            conn.commit()


def test_start_and_finish_task_wrappers_record_operating_loop() -> None:
    settings = get_settings()
    workspace_uri = f"file:///tmp/geond-task-wrapper-test-{uuid4()}"

    try:
        conn = connect(settings)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres integration database is not available: {exc}")

    with conn:
        try:
            run_schema_file(conn, SCHEMA)
        except psycopg.Error as exc:
            pytest.skip(f"Postgres integration schema is not available: {exc}")

        workspace_id = upsert_workspace(
            conn,
            root_uri=workspace_uri,
            name="task-wrapper-fixture",
            metadata={"source": "pytest"},
        )
        try:
            dry_run = start_task(
                conn,
                workspace_id,
                agent_name="agent-a",
                intent="Add operating loop wrappers",
                file_paths=["src/geond/cli_tasks.py"],
                reserve=True,
                dry_run=True,
            )
            before_events = list_reservation_events(conn, workspace_id)
            after_dry_run = list_reservation_events(conn, workspace_id)
            started = start_task(
                conn,
                workspace_id,
                agent_name="agent-a",
                intent="Add operating loop wrappers",
                file_paths=["src/geond/cli_tasks.py"],
                reserve=True,
            )
            active = list_active_file_reservations(
                conn,
                workspace_id,
                ["src/geond/cli_tasks.py"],
            )
            finished = finish_task(
                conn,
                workspace_id,
                agent_name="agent-a",
                summary="Added operating loop wrappers.",
                changed_files=[{"file_path": "src/geond/cli_tasks.py", "status": "added"}],
                tested_commands=["uv run pytest tests/test_cli_tasks.py"],
                remaining_risks=["Usage accounting remains a follow-up phase."],
                reservation_mode="release",
            )
            after_release = list_active_file_reservations(
                conn,
                workspace_id,
                ["src/geond/cli_tasks.py"],
            )
            handoffs = list_handoff_summaries(conn, workspace_id, status="open")
            events = list_reservation_events(conn, workspace_id)

            assert dry_run["status"] == "dry_run"
            assert dry_run["action_id"] is None
            assert after_dry_run == before_events
            assert started["status"] == "ok"
            assert started["action_id"]
            assert active[0]["agent_name"] == "agent-a"
            assert finished["status"] == "ok"
            assert finished["action_id"]
            assert finished["changeset"]["changeset_id"]
            assert finished["handoff_id"]
            assert after_release == []
            assert handoffs[0]["handoff_id"] == finished["handoff_id"]
            assert {event["action"] for event in events} >= {"created", "released"}
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM workspaces WHERE id = %s", (workspace_id,))
            conn.commit()
