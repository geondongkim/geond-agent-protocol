from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

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
