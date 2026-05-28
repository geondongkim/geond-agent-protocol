from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from geond.config import get_settings
from geond.db import connect, run_schema_file
from geond.storage.benchmark import (
    benchmark_search,
    compare_agent_run_benchmark_runs,
    compare_benchmark_runs,
    format_combined_benchmark_report_markdown,
    save_agent_run_benchmark,
    save_benchmark_run,
)
from geond.storage.maintenance import seed_sample_workspace
from geond.storage.repository import register_workspace_alias

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
        alias_uri = workspace_uri.replace("benchmark-test", "benchmark-alias")
        register_workspace_alias(
            conn,
            workspace_id_or_uri=workspace_uri,
            alias_uri=alias_uri,
            reason="pytest-benchmark-alias",
        )

        try:
            result = benchmark_search(
                conn,
                ["app_context"],
                mode="keyword",
                repeat=1,
                workspace_uri=alias_uri,
            )
            run_id = save_benchmark_run(
                conn,
                result,
                label="pytest",
                workspace_uri=alias_uri,
                provider="none",
                model="none",
            )
            report = compare_benchmark_runs(conn, workspace_uri=alias_uri)

            assert run_id
            assert report["runs"][0]["label"] == "pytest"
            assert report["runs"][0]["query_count"] == 1
            with conn.cursor() as cur:
                cur.execute("SELECT kind FROM benchmark_runs WHERE id = %s::uuid", (run_id,))
                assert cur.fetchone()[0] == "search"
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM workspaces WHERE root_uri = %s", (workspace_uri,))
            conn.commit()


def test_agent_run_benchmark_can_be_saved_and_reported() -> None:
    settings = get_settings()
    workspace_uri = f"file:///tmp/geond-agent-run-test-{uuid4()}"

    try:
        conn = connect(settings)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres integration database is not available: {exc}")

    with conn:
        try:
            run_schema_file(conn, SCHEMA)
        except psycopg.Error as exc:
            pytest.skip(f"Postgres integration schema is not available: {exc}")

        workspace_id = seed_sample_workspace(conn)["workspace_id"]
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE workspaces SET root_uri = %s WHERE id = %s",
                (workspace_uri, workspace_id),
            )
        conn.commit()
        try:
            run_id = save_agent_run_benchmark(
                conn,
                workspace_uri=workspace_uri,
                agent="codex",
                command="codex exec smoke",
                label="agent-smoke",
                prompt_text="hello",
                prompt_label="smoke.txt",
                wall_time_ms=123.4,
                provider="openai",
                model="gpt-test",
                final_output="SECRET_TOKEN=abc123456789abcdef",
                stdout_bytes=0,
                transcript_paths=["codex.final.txt"],
                token_usage={"total_tokens": 42},
            )
            report = compare_agent_run_benchmark_runs(conn, workspace_uri=workspace_uri)

            assert run_id
            assert report["runs"][0]["agent"] == "codex"
            assert report["runs"][0]["wall_time_ms"] == 123.4
            assert report["runs"][0]["stdout_bytes"] == 0
            assert report["runs"][0]["transcript_path"] == "codex.final.txt"
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM workspaces WHERE id = %s", (workspace_id,))
            conn.commit()


def test_combined_benchmark_report_markdown_keeps_kind_sections_separate() -> None:
    markdown = format_combined_benchmark_report_markdown(
        {
            "search": {
                "runs": [
                    {
                        "label": "search-smoke",
                        "mode": "keyword",
                        "query_count": 1,
                        "total_results": 2,
                    }
                ]
            },
            "agent_run": {
                "runs": [
                    {
                        "label": "agent-smoke",
                        "agent": "antigravity",
                        "wall_time_ms": 123.4,
                    }
                ]
            },
        }
    )

    assert "# Benchmark Report" in markdown
    assert "# Agent Run Benchmark Report" in markdown
    assert "search-smoke" in markdown
    assert "agent-smoke" in markdown
