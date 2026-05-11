from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from geond.code_graph.python_indexer import index_python_file
from geond.config import get_settings
from geond.db import connect, run_schema_file
from geond.retrieval.simple import get_symbol_context
from geond.storage.code_graph import store_code_index
from geond.storage.repository import upsert_workspace

SCHEMA = Path(__file__).parents[1] / "schemas" / "001_initial.sql"


def test_index_python_file_extracts_entities_and_edges(tmp_path: Path) -> None:
    source = tmp_path / "sample.py"
    source.write_text(
        """
import json

class Runner:
    def run(self, value):
        return helper(value)

def helper(value):
    return json.dumps({"value": value})
""".strip(),
        encoding="utf-8",
    )

    indexed = index_python_file(source, tmp_path)

    entities = {entity.qualified_name: entity for entity in indexed.entities}
    edge_types = {
        (edge.source_qualified_name, edge.target_qualified_name, edge.edge_type)
        for edge in indexed.edges
    }

    assert "sample" in entities
    assert "sample.Runner" in entities
    assert "sample.Runner.run" in entities
    assert "sample.helper" in entities
    assert entities["sample.Runner.run"].kind == "method"
    assert ("sample.Runner", "sample.Runner.run", "contains") in edge_types
    assert ("sample.Runner.run", "sample.helper", "calls") in edge_types


def test_store_python_index_supports_symbol_context(tmp_path: Path) -> None:
    settings = get_settings()
    workspace_uri = f"file:///tmp/geond-python-index-test-{uuid4()}"
    source = tmp_path / "package" / "service.py"
    source.parent.mkdir()
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
            name="python-index-fixture",
            metadata={"source": "pytest"},
        )
        try:
            stats = store_code_index(conn, workspace_id, [index_python_file(source, tmp_path)])
            results = get_symbol_context(conn, "build_answer", limit=5)

            assert stats["entities"] >= 2
            assert any(
                result["qualified_name"] == "package.service.build_answer" for result in results
            )
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM workspaces WHERE id = %s", (workspace_id,))
            conn.commit()
