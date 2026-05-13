from __future__ import annotations

from geond.dashboard_server import (
    dashboard_index,
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
    assert match_workspace_route("/api/workspaces/abc/write") is None
    assert query_limit("limit=5") == 5
    assert query_limit("limit=9999") == 500
    assert query_limit("limit=nope") == 100
    assert dashboard_index()["read_only"] is True
    assert status_for_payload({"status": "not_found"}) == 404
    assert status_for_payload({"status": "ok"}) == 200
