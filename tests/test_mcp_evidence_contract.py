"""Client-level contract test for the geond.evidence.v1 schema.

This test exercises every MCP tool that is expected to return evidence refs
and asserts that the returned data conforms to the published evidence
contract. The goal is to prevent silent regressions where a new field is
added or a schema marker is dropped without updating both the server and
documented client expectations.

The MCP tools are invoked directly as Python callables (FastMCP preserves
the wrapped function) so the test can run without a transport layer.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from geond.code_graph.python_indexer import index_python_file
from geond.config import get_settings
from geond.db import connect, run_schema_file
from geond.mcp_server import server as mcp_server
from geond.storage.code_graph import store_code_index
from geond.storage.repository import record_changeset, upsert_workspace

SCHEMA = Path(__file__).parents[1] / "schemas" / "001_initial.sql"
EVIDENCE_SCHEMA = "geond.evidence.v1"
EVIDENCE_REQUIRED_KEYS = {"schema", "kind", "locator", "metadata"}


def _assert_evidence_ref(ref: dict, expected_kinds: set[str]) -> None:
    assert ref is not None, "evidence ref must be present"
    missing = EVIDENCE_REQUIRED_KEYS - set(ref.keys())
    assert not missing, f"evidence ref missing keys: {missing}"
    assert ref["schema"] == EVIDENCE_SCHEMA
    assert ref["kind"] in expected_kinds, (
        f"unexpected kind {ref['kind']!r}, expected one of {expected_kinds}"
    )
    assert ref.get("target_id"), "evidence ref must include target_id"
    assert isinstance(ref["locator"], dict)
    assert isinstance(ref["metadata"], dict)


def _ensure_schema(conn: psycopg.Connection) -> None:
    try:
        run_schema_file(conn, SCHEMA)
    except psycopg.Error as exc:  # pragma: no cover - environment guard
        pytest.skip(f"Postgres integration schema is not available: {exc}")


def test_mcp_tools_return_geond_evidence_v1_contract(tmp_path: Path) -> None:
    settings = get_settings()
    workspace_uri = f"file:///tmp/geond-mcp-contract-{uuid4()}"
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
    except psycopg.OperationalError as exc:  # pragma: no cover - environment guard
        pytest.skip(f"Postgres integration database is not available: {exc}")

    with conn:
        _ensure_schema(conn)
        workspace_id = upsert_workspace(
            conn,
            root_uri=workspace_uri,
            name="mcp-contract-fixture",
            metadata={"source": "pytest"},
        )

        try:
            store_code_index(conn, workspace_id, [index_python_file(source, tmp_path)])
            recorded = record_changeset(
                conn,
                workspace_id=workspace_id,
                files=[
                    {
                        "file_path": "package/service.py",
                        "status": "modified",
                        "patch": patch,
                    }
                ],
                git_commit="abcdef1234567890",
                branch="main",
                intent="contract test",
                summary="Upper-case the build_answer output.",
            )
            changeset_id = recorded["changeset_id"]

            symbol_results = mcp_server.get_symbol_context("build_answer", limit=5)
            assert symbol_results, "symbol context must return at least one entity"
            for entity in symbol_results:
                _assert_evidence_ref(entity["evidence"], expected_kinds={"code_entity"})
                for related in entity.get("related_changesets", []):
                    _assert_evidence_ref(related["evidence"], expected_kinds={"changeset"})

            change_context = mcp_server.explain_change(
                "package/service.py", limit=5, include_narrative=True
            )
            for changeset in change_context.get("changesets", []):
                _assert_evidence_ref(changeset["evidence"], expected_kinds={"changeset"})
            for entity in change_context.get("touched_entities", []):
                _assert_evidence_ref(entity["evidence"], expected_kinds={"code_entity"})
            for snapshot in change_context.get("snapshots", []):
                _assert_evidence_ref(snapshot["evidence"], expected_kinds={"file_snapshot"})
            for message in change_context.get("related_messages", []):
                _assert_evidence_ref(message["evidence"], expected_kinds={"message"})

            narrative = change_context.get("narrative")
            assert narrative is not None
            assert narrative["schema"] == EVIDENCE_SCHEMA + ".narrative"
            assert narrative["citations"], "narrative must cite at least one evidence ref"
            cited_pointers = {citation["pointer"] for citation in narrative["citations"]}
            assert cited_pointers, "narrative citations must include pointer strings"

            detail = mcp_server.get_changeset_detail(changeset_id, include_narrative=True)
            assert detail["found"] is True
            _assert_evidence_ref(detail["evidence"], expected_kinds={"changeset"})
            assert detail["files"], "changeset detail must include files"
            for file_row in detail["files"]:
                _assert_evidence_ref(file_row["evidence"], expected_kinds={"change_file"})
            for entity in detail["touched_entities"]:
                _assert_evidence_ref(entity["evidence"], expected_kinds={"code_entity"})
            assert detail["narrative"]["schema"] == EVIDENCE_SCHEMA + ".narrative"

            commit_detail = mcp_server.get_changeset_detail("abcdef12", include_narrative=False)
            assert commit_detail["found"] is True
            assert commit_detail["changeset_id"] == changeset_id
            assert commit_detail.get("narrative") is None
            _assert_evidence_ref(commit_detail["evidence"], expected_kinds={"changeset"})

            missing = mcp_server.get_changeset_detail("0" * 8)
            assert missing["found"] is False
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM workspaces WHERE id = %s", (workspace_id,))
            conn.commit()
