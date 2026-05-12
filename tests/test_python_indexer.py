from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from geond.code_graph.python_indexer import index_python_file
from geond.config import get_settings
from geond.db import connect, run_schema_file
from geond.retrieval.simple import explain_change, get_symbol_context
from geond.storage.changesets import parse_unified_diff_line_ranges
from geond.storage.code_graph import store_code_index
from geond.storage.repository import record_changeset, upsert_workspace

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


def test_changesets_link_to_indexed_symbols_and_explain_change(tmp_path: Path) -> None:
    settings = get_settings()
    workspace_uri = f"file:///tmp/geond-changeset-link-test-{uuid4()}"
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
            name="changeset-link-fixture",
            metadata={"source": "pytest"},
        )
        try:
            store_code_index(conn, workspace_id, [index_python_file(source, tmp_path)])
            changeset = record_changeset(
                conn,
                workspace_id=workspace_id,
                files=[{"file_path": "package/service.py", "status": "modified"}],
                intent="test symbol evidence",
                summary="Updated build_answer behavior.",
            )
            symbol_context = get_symbol_context(conn, "build_answer", limit=5)
            change_context = explain_change(conn, "package/service.py", limit=5)

            answer_context = next(
                item
                for item in symbol_context
                if item["qualified_name"] == "package.service.build_answer"
            )
            assert changeset["linked_change_entities"] >= 1
            assert answer_context["evidence"]["kind"] == "code_entity"
            assert (
                answer_context["related_changesets"][0]["changeset_id"] == changeset["changeset_id"]
            )
            assert change_context["changesets"][0]["summary"] == "Updated build_answer behavior."
            assert any(
                item["qualified_name"] == "package.service.build_answer"
                for item in change_context["touched_entities"]
            )
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM workspaces WHERE id = %s", (workspace_id,))
            conn.commit()


def test_changesets_link_patch_hunks_to_symbol_line_ranges(tmp_path: Path) -> None:
    settings = get_settings()
    workspace_uri = f"file:///tmp/geond-changeset-hunk-test-{uuid4()}"
    source = tmp_path / "package" / "service.py"
    source.parent.mkdir()
    source.write_text(
        """
def build_answer(prompt):
    return prompt.strip().upper()

def helper(value):
    return value.strip()
""".strip(),
        encoding="utf-8",
    )
    patch = """
diff --git a/package/service.py b/package/service.py
--- a/package/service.py
+++ b/package/service.py
@@ -1,5 +1,5 @@
def build_answer(prompt):
-    return prompt.strip()
+    return prompt.strip().upper()
def helper(value):
    return value.strip()
""".strip()

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
            name="changeset-hunk-fixture",
            metadata={"source": "pytest"},
        )
        try:
            store_code_index(conn, workspace_id, [index_python_file(source, tmp_path)])
            changeset = record_changeset(
                conn,
                workspace_id=workspace_id,
                files=[
                    {
                        "file_path": "package/service.py",
                        "status": "modified",
                        "patch": patch,
                    }
                ],
                intent="test hunk symbol evidence",
                summary="Updated build_answer line only.",
            )
            answer_context = next(
                item
                for item in get_symbol_context(conn, "build_answer", limit=5)
                if item["qualified_name"] == "package.service.build_answer"
            )
            helper_context = next(
                item
                for item in get_symbol_context(conn, "helper", limit=5)
                if item["qualified_name"] == "package.service.helper"
            )
            change_context = explain_change(conn, "package/service.py", limit=5)
            touched_names = {item["qualified_name"] for item in change_context["touched_entities"]}

            assert changeset["linked_change_entities"] == 1
            assert answer_context["related_changesets"][0]["match_type"] == "line_range"
            assert answer_context["related_changesets"][0]["metadata"]["changed_start_line"] == 2
            assert helper_context["related_changesets"] == []
            assert "package.service.build_answer" in touched_names
            assert "package.service.helper" not in touched_names
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM workspaces WHERE id = %s", (workspace_id,))
            conn.commit()


def test_parse_unified_diff_line_ranges_uses_changed_new_lines() -> None:
    patch = """
@@ -10,4 +10,5 @@
 def run():
-    return old()
+    value = new()
+    return value
""".strip()

    ranges = parse_unified_diff_line_ranges(patch)

    assert len(ranges) == 1
    assert ranges[0].new_start_line == 10
    assert ranges[0].new_end_line == 14
    assert ranges[0].changed_start_line == 11
    assert ranges[0].changed_end_line == 12
