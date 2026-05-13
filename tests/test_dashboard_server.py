from __future__ import annotations

from geond.config import Settings
from geond.dashboard_server import (
    dashboard_index,
    dashboard_response,
    database_connection_info,
    match_workspace_route,
    query_limit,
    status_for_payload,
)


def test_dashboard_route_matching_and_index() -> None:
    assert match_workspace_route("/api/workspaces/abc-123/overview") == ("abc-123", "overview")
    assert match_workspace_route("/api/workspaces/file%3A%2F%2F%2Ftmp%2Fdemo/activity") == (
        "file:///tmp/demo",
        "activity",
    )
    assert match_workspace_route("/api/workspaces/abc-123/sessions") == ("abc-123", "sessions")
    assert match_workspace_route("/api/workspaces/abc/write") is None
    assert query_limit("limit=5") == 5
    assert query_limit("limit=9999") == 500
    assert query_limit("limit=nope") == 100
    assert dashboard_index()["read_only"] is True
    assert status_for_payload({"status": "not_found"}) == 404
    assert status_for_payload({"status": "ok"}) == 200


def test_dashboard_root_serves_html_without_database() -> None:
    status, content_type, body = dashboard_response(Settings(), "/")

    assert status == 200
    assert content_type == "text/html; charset=utf-8"
    assert b"Geond Agent Activity" in body
    assert b"/api/workspaces/" in body
    assert b"Mission Control" in body
    assert b"agent-board" in body
    assert b"database-badge" in body
    assert b"agent-switchboard" in body
    assert b"session-summary" in body
    assert b"Readable Excerpts" in body
    assert b"Coordination Readiness" in body


def test_dashboard_database_info_classifies_local_and_azure() -> None:
    local = database_connection_info(
        Settings(database_url="postgresql://user:secret@localhost:55432/geond")
    )
    azure = database_connection_info(
        Settings(
            database_url=(
                "postgresql://geondadmin:secret@"
                "pg-geond-team.postgres.database.azure.com:5432/geond?sslmode=require"
            )
        )
    )

    assert local == {
        "source": "local",
        "label": "Local PostgreSQL",
        "host": "localhost",
        "database": "geond",
        "sslmode": None,
    }
    assert azure == {
        "source": "azure-postgresql",
        "label": "Azure PostgreSQL",
        "host": "pg-geond-team.postgres.database.azure.com",
        "database": "geond",
        "sslmode": "require",
    }
    assert "secret" not in str(local)
    assert "secret" not in str(azure)


def test_dashboard_index_includes_safe_database_metadata() -> None:
    index = dashboard_index(
        Settings(
            database_url=(
                "postgresql://geondadmin:secret@"
                "pg-geond-team.postgres.database.azure.com:5432/geond?sslmode=require"
            )
        )
    )

    assert index["database"]["source"] == "azure-postgresql"
    assert index["database"]["host"] == "pg-geond-team.postgres.database.azure.com"
    assert "secret" not in str(index)
