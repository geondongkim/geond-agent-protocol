from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from geond.code_graph.ts_js_indexer import index_ts_js_file
from geond.config import get_settings
from geond.db import connect, run_schema_file
from geond.retrieval.simple import get_symbol_context
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
    assert entities["service"].end_line == 13
    assert entities["service.buildAnswer"].end_line == 5
    assert entities["service.normalize"].end_line == 7
    assert entities["service.Reporter"].end_line == 13
    assert entities["service.Reporter.report"].end_line == 12
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


def test_store_ts_js_index_resolves_cross_file_import_calls(tmp_path: Path) -> None:
    settings = get_settings()
    workspace_uri = f"file:///tmp/geond-ts-cross-file-test-{uuid4()}"
    text = tmp_path / "text.ts"
    service = tmp_path / "service.ts"
    text.write_text(
        """
export function trim(value: string): string {
  return value.trim();
}

export const title = (value: string) => trim(value).toUpperCase();
""".strip(),
        encoding="utf-8",
    )
    service.write_text(
        """
import { trim as clean } from "./text";
import * as text from "./text";

export function buildAnswer(prompt: string): string {
  return clean(prompt);
}

export function buildTitle(prompt: string): string {
  return text.title(prompt);
}
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
            name="ts-cross-file-fixture",
            metadata={"source": "pytest"},
        )
        try:
            stats = store_code_index(
                conn,
                workspace_id,
                [index_ts_js_file(text, tmp_path), index_ts_js_file(service, tmp_path)],
            )
            trim_context = next(
                item
                for item in get_symbol_context(conn, "trim", limit=10)
                if item["qualified_name"] == "text.trim"
            )
            title_context = next(
                item
                for item in get_symbol_context(conn, "title", limit=10)
                if item["qualified_name"] == "text.title"
            )
            answer_context = next(
                item
                for item in get_symbol_context(conn, "buildAnswer", limit=10)
                if item["qualified_name"] == "service.buildAnswer"
            )
            title_builder_context = next(
                item
                for item in get_symbol_context(conn, "buildTitle", limit=10)
                if item["qualified_name"] == "service.buildTitle"
            )

            assert stats["edges"] >= 2
            clean_call = next(
                caller
                for caller in trim_context["callers"]
                if caller["qualified_name"] == "service.buildAnswer"
            )
            assert clean_call["edge"]["metadata"] == {
                "call": "clean",
                "resolution": "import_qualified_name_match",
            }
            assert answer_context["callees"][0]["qualified_name"] == "text.trim"
            assert title_context["callers"][0]["qualified_name"] == "service.buildTitle"
            assert title_builder_context["callees"][0]["qualified_name"] == "text.title"
            assert title_builder_context["callees"][0]["edge"]["evidence"]["schema"] == (
                "geond.evidence.v1"
            )
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM workspaces WHERE id = %s", (workspace_id,))
            conn.commit()


def test_store_ts_js_index_resolves_default_import_calls(tmp_path: Path) -> None:
    settings = get_settings()
    workspace_uri = f"file:///tmp/geond-ts-default-import-test-{uuid4()}"
    text = tmp_path / "text.ts"
    service = tmp_path / "service.ts"
    text.write_text(
        """
export default function trim(value: string): string {
  return value.trim();
}
""".strip(),
        encoding="utf-8",
    )
    service.write_text(
        """
import clean from "./text";

export function buildAnswer(prompt: string): string {
  return clean(prompt);
}
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
            name="ts-default-import-fixture",
            metadata={"source": "pytest"},
        )
        try:
            stats = store_code_index(
                conn,
                workspace_id,
                [index_ts_js_file(text, tmp_path), index_ts_js_file(service, tmp_path)],
            )
            trim_context = next(
                item
                for item in get_symbol_context(conn, "trim", limit=10)
                if item["qualified_name"] == "text.trim"
            )
            answer_context = next(
                item
                for item in get_symbol_context(conn, "buildAnswer", limit=10)
                if item["qualified_name"] == "service.buildAnswer"
            )

            assert stats["edges"] >= 1
            default_call = next(
                caller
                for caller in trim_context["callers"]
                if caller["qualified_name"] == "service.buildAnswer"
            )
            assert default_call["edge"]["metadata"] == {
                "call": "clean",
                "resolution": "import_qualified_name_match",
            }
            assert answer_context["callees"][0]["qualified_name"] == "text.trim"
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM workspaces WHERE id = %s", (workspace_id,))
            conn.commit()
