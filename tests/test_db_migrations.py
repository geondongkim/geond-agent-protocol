from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from geond.config import get_settings
from geond.db import connect, discover_schema_files, run_schema_file, run_schema_migrations

SCHEMA = Path(__file__).parents[1] / "schemas" / "001_initial.sql"


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
