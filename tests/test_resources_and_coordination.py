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
    list_active_file_reservations,
    record_agent_action,
    release_reservation,
    reserve_files,
    upsert_workspace,
)
from geond.storage.resources import get_symbol_resource, get_workspace_timeline

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
            active = list_active_file_reservations(conn, workspace_id)
            record_agent_action(
                conn,
                workspace_id=workspace_id,
                agent_name="agent-a",
                action_type="index",
                summary="Indexed service.py",
            )
            timeline = get_workspace_timeline(conn, workspace_id)
            released = release_reservation(
                conn,
                workspace_id,
                reservation_id=first["reservation_ids"][0],
            )

            assert any(
                entity["qualified_name"] == "service.build_answer" for entity in symbol["entities"]
            )
            assert first["conflicts"] == []
            assert second["conflicts"][0]["agent_name"] == "agent-a"
            assert len(active) == 2
            assert any(event["kind"] == "agent_action" for event in timeline["events"])
            assert any(event["kind"] == "file_reservation" for event in timeline["events"])
            assert released == 1
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM workspaces WHERE id = %s", (workspace_id,))
            conn.commit()
