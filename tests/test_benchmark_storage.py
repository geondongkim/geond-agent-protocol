from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from geond.config import get_settings
from geond.db import connect, run_schema_file
from geond.storage.benchmark import (
    benchmark_search,
    compare_benchmark_runs,
    save_benchmark_run,
)
from geond.storage.maintenance import seed_sample_workspace

SCHEMA = Path(__file__).parents[1] / "schemas" / "001_initial.sql"


def test_benchmark_runs_can_be_saved_and_compared() -> None:
    settings = get_settings()
    workspace_uri = f"file:///tmp/geond-benchmark-test-{uuid4()}"

    try:
        conn = connect(settings)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres integration database is not available: {exc}")

    with conn:
        try:
            run_schema_file(conn, SCHEMA)
        except psycopg.Error as exc:
            pytest.skip(f"Postgres integration schema is not available: {exc}")

        seeded = seed_sample_workspace(conn)
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE workspaces SET root_uri = %s WHERE id = %s",
                (workspace_uri, seeded["workspace_id"]),
            )
        conn.commit()

        try:
            result = benchmark_search(
                conn,
                ["app_context"],
                mode="keyword",
                repeat=1,
                workspace_uri=workspace_uri,
            )
            run_id = save_benchmark_run(
                conn,
                result,
                label="pytest",
                workspace_uri=workspace_uri,
                provider="none",
                model="none",
            )
            report = compare_benchmark_runs(conn, workspace_uri=workspace_uri)

            assert run_id
            assert report["runs"][0]["label"] == "pytest"
            assert report["runs"][0]["query_count"] == 1
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM workspaces WHERE root_uri = %s", (workspace_uri,))
            conn.commit()
