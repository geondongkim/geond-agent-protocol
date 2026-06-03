from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from geond.config import get_settings
from geond.db import connect, discover_schema_files, run_schema_file, run_schema_migrations

SCHEMA = Path(__file__).parents[1] / "schemas" / "001_initial.sql"
ORCHESTRATION_SCHEMA = Path(__file__).parents[1] / "schemas" / "007_orchestration.sql"
TASK_GRAPH_SCHEMA = Path(__file__).parents[1] / "schemas" / "008_orchestration_task_graph.sql"
TASK_GRAPH_UNIQUE_INDEX = "orchestration_task_edges_from_task_id_to_task_id_edge_type_key"


def test_discover_schema_files_sorts_sql_files(tmp_path: Path) -> None:
    first = tmp_path / "002_second.sql"
    second = tmp_path / "001_first.sql"
    ignored = tmp_path / "notes.md"
    first.write_text("SELECT 2;", encoding="utf-8")
    second.write_text("SELECT 1;", encoding="utf-8")
    ignored.write_text("ignore", encoding="utf-8")

    assert discover_schema_files(tmp_path) == [second, first]


def test_run_schema_migrations_skips_applied_files(tmp_path: Path) -> None:
    settings = get_settings()
    try:
        conn = connect(settings)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres integration database is not available: {exc}")

    marker = f"geond_migration_probe_{uuid4().hex}"
    migration = tmp_path / f"999_{marker}.sql"
    migration.write_text(
        f"""
        CREATE TABLE IF NOT EXISTS {marker} (id text PRIMARY KEY);
        INSERT INTO {marker} (id) VALUES ('one') ON CONFLICT DO NOTHING;
        """,
        encoding="utf-8",
    )

    with conn:
        try:
            run_schema_file(conn, SCHEMA)
            first = run_schema_migrations(conn, [migration])
            second = run_schema_migrations(conn, [migration])
            with conn.cursor() as cur:
                cur.execute(f"SELECT count(*) FROM {marker}")
                row_count = cur.fetchone()[0]
            assert first[0]["status"] == "applied"
            assert second[0]["status"] == "skipped"
            assert row_count == 1
        except psycopg.Error as exc:
            pytest.skip(f"Postgres integration schema is not available: {exc}")
        finally:
            with conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS {marker}")
                cur.execute(
                    "DELETE FROM schema_migrations WHERE id = %s",
                    (migration.stem,),
                )
            conn.commit()


def test_orchestration_schema_adds_worker_lease_contract() -> None:
    settings = get_settings()
    try:
        conn = connect(settings)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres integration database is not available: {exc}")

    with conn:
        try:
            run_schema_file(conn, SCHEMA)
            run_schema_file(conn, ORCHESTRATION_SCHEMA)
            run_schema_file(conn, ORCHESTRATION_SCHEMA)
            run_schema_file(conn, TASK_GRAPH_SCHEMA)
            run_schema_file(conn, TASK_GRAPH_SCHEMA)
        except psycopg.Error as exc:
            pytest.skip(f"Postgres integration schema is not available: {exc}")

        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.orchestration_runs')::text")
            assert cur.fetchone()[0] == "orchestration_runs"
            cur.execute("SELECT to_regclass('public.task_leases')::text")
            assert cur.fetchone()[0] == "task_leases"
            cur.execute("SELECT to_regclass('public.idx_task_leases_one_active_per_task')::text")
            assert cur.fetchone()[0] == "idx_task_leases_one_active_per_task"
            cur.execute("SELECT to_regclass('public.orchestration_task_edges')::text")
            assert cur.fetchone()[0] == "orchestration_task_edges"
            cur.execute("SELECT to_regclass(%s)::text", (f"public.{TASK_GRAPH_UNIQUE_INDEX}",))
            assert cur.fetchone()[0] == TASK_GRAPH_UNIQUE_INDEX
            cur.execute("SELECT to_regclass('public.idempotency_records')::text")
            assert cur.fetchone()[0] == "idempotency_records"
