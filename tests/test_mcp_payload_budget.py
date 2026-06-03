from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from geond.config import get_settings
from geond.db import connect, run_schema_file
from geond.mcp_server import server as mcp_server
from geond.retrieval.simple import search_dev_memory
from geond.storage.context_review import review_workspace_context
from geond.storage.maintenance import seed_sample_workspace
from geond.storage.resources import get_session_resource

SCHEMA = Path(__file__).parents[1] / "schemas" / "001_initial.sql"


def _json_size(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


def test_search_dev_memory_mcp_schema_defaults_to_keyword() -> None:
    tool = mcp_server.mcp._tool_manager._tools["search_dev_memory"]

    assert tool.parameters["properties"]["mode"]["default"] == "keyword"


def test_mcp_tool_metadata_is_glama_ready() -> None:
    tools = mcp_server.mcp._tool_manager._tools

    assert "get_geond_server_info" in tools
    for name, tool in tools.items():
        assert "Purpose:" in tool.description, name
        assert "When to use:" in tool.description, name
        for param_name, param_schema in tool.parameters.get("properties", {}).items():
            assert param_schema.get("description"), f"{name}.{param_name}"


def test_get_geond_server_info_does_not_require_database() -> None:
    info = mcp_server.get_geond_server_info()

    assert info["name"] == "Geond Agent Protocol"
    assert info["database_required"] is False
    assert info["safe_for_browser_try"] is True
    assert "GEOND_DATABASE_URL" in info["environment_variables"]["optional"]
    assert any(group["name"] == "coordination" for group in info["tool_groups"])


def test_common_mcp_payloads_stay_compact_by_default() -> None:
    settings = get_settings()
    workspace_uri = f"file:///tmp/geond-mcp-budget-test-{uuid4()}"

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
        workspace_id = seeded["workspace_id"]
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE workspaces SET root_uri = %s WHERE id = %s",
                (workspace_uri, workspace_id),
            )
            cur.execute(
                "SELECT external_id FROM sessions WHERE workspace_id = %s LIMIT 1",
                (workspace_id,),
            )
            session_external_id = cur.fetchone()[0]
        conn.commit()
        try:
            search_payload = search_dev_memory(
                conn,
                "app_context",
                workspace_uri=workspace_uri,
                limit=5,
            )
            review_payload = review_workspace_context(
                conn,
                workspace_uri,
                intent="inspect app_context",
                limit=5,
            )
            session_payload = get_session_resource(conn, session_external_id, limit=10)

            assert _json_size(search_payload) < 20_000
            assert _json_size(review_payload) < 30_000
            assert _json_size(session_payload) < 15_000
            assert "content" not in session_payload["messages"][0]
            assert "snippet" in session_payload["messages"][0]
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM workspaces WHERE id = %s", (workspace_id,))
            conn.commit()
