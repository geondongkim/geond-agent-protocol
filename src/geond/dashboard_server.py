from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from geond.config import Settings
from geond.db import connect
from geond.storage.dashboard import get_agent_activity_events, get_dashboard_overview
from geond.storage.resources import (
    get_workspace_handoffs,
    get_workspace_lineage,
    get_workspace_reservations,
    get_workspace_timeline,
)


def serve_dashboard(
    settings: Settings,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_url: bool = False,
) -> None:
    handler = dashboard_handler(settings)
    server = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{port}"
    print(f"Geond dashboard API listening on {url}")
    print("Read-only endpoints: /health and /api/workspaces/{workspace_id}/overview")
    if open_url:
        open_browser(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Geond dashboard API")
    finally:
        server.server_close()


def dashboard_handler(settings: Settings) -> type[BaseHTTPRequestHandler]:
    class DashboardHandler(BaseHTTPRequestHandler):
        server_version = "GeondDashboard/0.1"

        def do_GET(self) -> None:  # noqa: N802
            status, payload = dashboard_payload(settings, self.path)
            body = json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            return

    return DashboardHandler


def dashboard_payload(settings: Settings, path: str) -> tuple[int, dict[str, Any]]:
    parsed = urlparse(path)
    if parsed.path in {"", "/"}:
        return 200, dashboard_index()
    if parsed.path == "/health":
        return 200, {"status": "ok", "service": "geond-dashboard"}

    route = match_workspace_route(parsed.path)
    if route is None:
        return 404, {"status": "not_found", "path": parsed.path}
    workspace_id, endpoint = route
    limit = query_limit(parsed.query)

    with connect(settings) as conn:
        if endpoint == "overview":
            payload = get_dashboard_overview(conn, workspace_id, limit=limit)
            return status_for_payload(payload), payload
        if endpoint == "activity":
            payload = get_agent_activity_events(conn, workspace_id, limit=limit)
            return status_for_payload(payload), payload
        if endpoint == "timeline":
            payload = get_workspace_timeline(conn, workspace_id, limit=limit)
            return status_for_payload(payload), payload
        if endpoint == "lineage":
            payload = get_workspace_lineage(conn, workspace_id, limit=limit)
            return status_for_payload(payload), payload
        if endpoint == "reservations":
            payload = get_workspace_reservations(conn, workspace_id)
            return status_for_payload(payload), payload
        if endpoint == "handoffs":
            status = parse_qs(parsed.query).get("status", [None])[0]
            payload = get_workspace_handoffs(conn, workspace_id, status=status, limit=limit)
            return status_for_payload(payload), payload

    return 404, {"status": "not_found", "workspace_id": workspace_id, "endpoint": endpoint}


def dashboard_index() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "geond-dashboard",
        "read_only": True,
        "endpoints": [
            "/health",
            "/api/workspaces/{workspace_id}/overview",
            "/api/workspaces/{workspace_id}/activity",
            "/api/workspaces/{workspace_id}/timeline",
            "/api/workspaces/{workspace_id}/lineage",
            "/api/workspaces/{workspace_id}/reservations",
            "/api/workspaces/{workspace_id}/handoffs",
        ],
    }


def match_workspace_route(path: str) -> tuple[str, str] | None:
    parts = [unquote(part) for part in path.strip("/").split("/") if part]
    if len(parts) != 4 or parts[0] != "api" or parts[1] != "workspaces":
        return None
    endpoint = parts[3]
    if endpoint not in {"overview", "activity", "timeline", "lineage", "reservations", "handoffs"}:
        return None
    return parts[2], endpoint


def query_limit(query: str, default: int = 100) -> int:
    values = parse_qs(query).get("limit")
    if not values:
        return default
    try:
        return max(1, min(int(values[0]), 500))
    except ValueError:
        return default


def status_for_payload(payload: dict[str, Any]) -> int:
    return 404 if payload.get("status") == "not_found" else 200


def open_browser(url: str) -> None:
    import webbrowser

    webbrowser.open(url)
