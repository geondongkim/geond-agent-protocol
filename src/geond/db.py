from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import psycopg
from psycopg import Connection

from geond.config import Settings, get_settings


def connect(settings: Settings | None = None) -> Connection:
    active_settings = settings or get_settings()
    return psycopg.connect(active_settings.database_url)


def run_sql_files(conn: Connection, paths: Iterable[Path]) -> None:
    with conn.cursor() as cur:
        for path in paths:
            cur.execute(path.read_text(encoding="utf-8"))
    conn.commit()


def run_schema_file(conn: Connection, schema_path: Path) -> None:
    run_sql_files(conn, [schema_path])


def discover_schema_files(schemas_dir: Path) -> list[Path]:
    return sorted(path for path in schemas_dir.glob("*.sql") if path.is_file())


def schema_migration_id(schema_path: Path) -> str:
    return schema_path.stem


def run_schema_migrations(conn: Connection, paths: Iterable[Path]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    with conn.cursor() as cur:
        for path in paths:
            migration_id = schema_migration_id(path)
            if schema_migration_applied(cur, migration_id):
                results.append(
                    {
                        "id": migration_id,
                        "path": str(path),
                        "status": "skipped",
                    }
                )
                continue

            cur.execute(path.read_text(encoding="utf-8"))
            cur.execute(
                """
                INSERT INTO schema_migrations (id)
                VALUES (%s)
                ON CONFLICT (id) DO NOTHING
                """,
                (migration_id,),
            )
            results.append(
                {
                    "id": migration_id,
                    "path": str(path),
                    "status": "applied",
                }
            )
    conn.commit()
    return results


def schema_migration_applied(cur: Any, migration_id: str) -> bool:
    cur.execute("SELECT to_regclass('public.schema_migrations')")
    if cur.fetchone()[0] is None:
        return False
    cur.execute("SELECT 1 FROM schema_migrations WHERE id = %s", (migration_id,))
    return cur.fetchone() is not None
