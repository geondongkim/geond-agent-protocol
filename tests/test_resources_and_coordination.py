from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from geond.code_graph.python_indexer import index_python_file
from geond.config import get_settings
from geond.db import connect, run_schema_file
from geond.storage.code_graph import store_code_index
from geond.storage.repository import (
    cleanup_expired_reservations_for_workspace,
    close_handoff_summary,
    list_active_file_reservations,
    list_active_symbol_reservations,
    list_handoff_summaries,
    list_reservation_events,
    record_agent_action,
    record_handoff_summary,
    release_reservation,
    release_symbol_reservation,
    renew_reservation,
    renew_symbol_reservation,
    reserve_files,
    reserve_symbols,
    upsert_workspace,
)
from geond.storage.resources import (
    get_symbol_resource,
    get_workspace_handoffs,
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
                blocked_on=[],
            )
            handoffs = list_handoff_summaries(conn, workspace_id, status="open")
            reservation_resource = get_workspace_reservations(conn, workspace_id)
            handoff_resource = get_workspace_handoffs(conn, workspace_id)
            timeline = get_workspace_timeline(conn, workspace_id)
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
