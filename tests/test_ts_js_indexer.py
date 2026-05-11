from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from geond.code_graph.ts_js_indexer import index_ts_js_file
from geond.config import get_settings
from geond.db import connect, run_schema_file
from geond.storage.code_graph import store_code_index
from geond.storage.repository import reserve_symbols, upsert_workspace

SCHEMA = Path(__file__).parents[1] / "schemas" / "001_initial.sql"


def test_index_ts_js_file_extracts_entities_and_edges(tmp_path: Path) -> None:
    source = tmp_path / "service.ts"
    source.write_text(
        """
import { trim } from "./text";

export function buildAnswer(prompt: string): string {
  return normalize(prompt);
}

const normalize = (value: string) => trim(value);

export class Reporter {
  report(prompt: string) {
    return buildAnswer(prompt);
  }
}
""".strip(),
        encoding="utf-8",
    )

    indexed = index_ts_js_file(source, tmp_path)

    entities = {entity.qualified_name: entity for entity in indexed.entities}
    edge_types = {
        (edge.source_qualified_name, edge.target_qualified_name, edge.edge_type)
        for edge in indexed.edges
    }

    assert "service" in entities
    assert "service.buildAnswer" in entities
    assert "service.normalize" in entities
    assert "service.Reporter" in entities
    assert "service.Reporter.report" in entities
    assert entities["service.buildAnswer"].metadata["language"] == "typescript"
    assert ("service.Reporter", "service.Reporter.report", "contains") in edge_types
    assert ("service.Reporter.report", "service.buildAnswer", "calls") in edge_types


def test_store_ts_js_index_supports_symbol_resolution(tmp_path: Path) -> None:
    settings = get_settings()
    workspace_uri = f"file:///tmp/geond-ts-index-test-{uuid4()}"
    source = tmp_path / "service.ts"
    source.write_text("export const buildAnswer = (prompt: string) => prompt.trim();", "utf-8")

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
            name="ts-index-fixture",
            metadata={"source": "pytest"},
        )
        try:
            store_code_index(conn, workspace_id, [index_ts_js_file(source, tmp_path)])
            reserved = reserve_symbols(
                conn,
                workspace_id,
                "agent-a",
                ["buildAnswer"],
                purpose="edit TS function",
            )

            assert reserved["resolved_symbols"]["buildAnswer"]["qualified_name"] == (
                "service.buildAnswer"
            )
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM workspaces WHERE id = %s", (workspace_id,))
            conn.commit()
