from __future__ import annotations

import hashlib
import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from geond import (
    orchestrator_action_bundle,
    orchestrator_action_queue,
    orchestrator_planner,
    orchestrator_scheduler,
)
from geond.config import Settings
from geond.db import connect
from geond.storage.dashboard import (
    get_agent_activity_events,
    get_dashboard_changesets,
    get_dashboard_code_risk,
    get_dashboard_orchestration,
    get_dashboard_orchestration_traces,
    get_dashboard_overview,
    get_dashboard_project_activity,
    get_dashboard_sessions,
    get_dashboard_usage,
    get_dashboard_workspaces,
)
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
    print("Dashboard UI: /?workspace=<workspace-id-or-uri>")
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
            status, content_type, body = dashboard_response(settings, self.path)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                return

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != "/api/webhooks/manus":
                body = json.dumps({"status": "not_found"}).encode("utf-8")
                self.send_response(404)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            status, payload = _handle_manus_webhook(settings, self.headers, self.rfile)
            body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                return

        def log_message(self, format: str, *args: Any) -> None:
            return

    return DashboardHandler


def _handle_manus_webhook(
    settings: Settings,
    headers: Any,
    rfile: Any,
) -> tuple[int, dict[str, Any]]:
    """Process a Manus webhook POST to /api/webhooks/manus.

    Validates HMAC-SHA256 signature when MANUS_WEBHOOK_SECRET is configured.
    Stores the task via store_manus_task on task.completed or task.failed events.
    """
    from geond.adapters.manus import normalize_task
    from geond.storage.repository import store_manus_task, upsert_workspace

    try:
        content_length = int(headers.get("Content-Length") or "0")
        if content_length > 1_048_576:
            return 413, {"status": "error", "message": "payload_too_large"}
        raw_body = rfile.read(content_length)
    except Exception:
        return 400, {"status": "error", "message": "unreadable_body"}

    if settings.manus_webhook_secret:
        sig_header = headers.get("x-manus-signature") or headers.get("X-Manus-Signature") or ""
        expected = hmac.new(
            settings.manus_webhook_secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(f"sha256={expected}", sig_header):
            return 401, {"status": "error", "message": "invalid_signature"}

    try:
        event = json.loads(raw_body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 400, {"status": "error", "message": "invalid_json"}

    event_type = str(event.get("event") or "")
    if event_type not in {"task.completed", "task.failed", "task.updated"}:
        return 200, {"status": "ignored", "event": event_type}

    task_data = event.get("task") or {}
    if not task_data:
        return 400, {"status": "error", "message": "missing_task_field"}

    workspace_uri = str(event.get("workspace_uri") or "")
    if not workspace_uri:
        return 400, {"status": "error", "message": "missing_workspace_uri"}

    task_messages = event.get("messages") or None
    try:
        task = normalize_task(task_data, task_messages)
    except Exception as exc:
        return 422, {"status": "error", "message": f"normalize_failed: {exc}"}

    try:
        with connect(settings) as conn:
            workspace_id = upsert_workspace(
                conn,
                root_uri=workspace_uri,
                metadata={"source": "manus_webhook"},
            )
            session_id = store_manus_task(conn, workspace_id, task)
    except Exception as exc:
        return 500, {"status": "error", "message": f"storage_failed: {exc}"}

    return 200, {
        "status": "ok",
        "event": event_type,
        "task_id": task.task_id,
        "session_id": session_id,
        "imported_messages": len(task.messages),
    }


def dashboard_response(settings: Settings, path: str) -> tuple[int, str, bytes]:
    parsed = urlparse(path)
    if parsed.path in {"", "/"}:
        return 200, "text/html; charset=utf-8", dashboard_html().encode("utf-8")
    status, payload = dashboard_payload(settings, path)
    body = json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    return status, "application/json; charset=utf-8", body


def dashboard_payload(settings: Settings, path: str) -> tuple[int, dict[str, Any]]:
    parsed = urlparse(path)
    if parsed.path == "/api":
        return 200, dashboard_index(settings)
    if parsed.path == "/health":
        return 200, {
            "status": "ok",
            "service": "geond-dashboard",
            "database": database_connection_info(settings),
        }
    if parsed.path == "/api/workspaces":
        limit = query_limit(parsed.query, default=250)
        with connect(settings) as conn:
            payload = get_dashboard_workspaces(conn, limit=limit)
        return status_for_payload(payload), payload

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
            query = parse_qs(parsed.query)
            payload = get_agent_activity_events(
                conn,
                workspace_id,
                limit=limit,
                event_kind=query.get("kind", [None])[0],
                agent_name=query.get("agent", [None])[0],
                status=query.get("status", [None])[0],
            )
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
        if endpoint == "sessions":
            message_limit = query_int(parsed.query, "message_limit", default=4, maximum=30)
            payload = get_dashboard_sessions(
                conn,
                workspace_id,
                limit=limit,
                message_limit=message_limit,
            )
            return status_for_payload(payload), payload
        if endpoint == "project":
            payload = get_dashboard_project_activity(conn, workspace_id, limit=limit)
            return status_for_payload(payload), payload
        if endpoint == "code-risk":
            payload = get_dashboard_code_risk(conn, workspace_id, limit=limit)
            return status_for_payload(payload), payload
        if endpoint == "changesets":
            payload = get_dashboard_changesets(conn, workspace_id, limit=limit)
            return status_for_payload(payload), payload
        if endpoint == "usage":
            payload = get_dashboard_usage(conn, workspace_id)
            return status_for_payload(payload), payload
        if endpoint == "orchestration":
            payload = get_dashboard_orchestration(conn, workspace_id, limit=limit)
            return status_for_payload(payload), payload
        if endpoint == "orchestration-plan":
            payload = orchestrator_planner.create_plan(
                conn,
                workspace_id_or_uri=workspace_id,
                limit=limit,
            )
            return status_for_payload(payload), payload
        if endpoint == "orchestration-actions":
            payload = orchestrator_action_bundle.build_action_bundle(
                conn,
                workspace_id_or_uri=workspace_id,
                limit=limit,
            )
            return status_for_payload(payload), payload
        if endpoint == "orchestration-action-queue":
            payload = orchestrator_action_queue.build_dashboard_action_queue(
                conn,
                workspace_id_or_uri=workspace_id,
                limit=limit,
            )
            return status_for_payload(payload), payload
        if endpoint == "orchestration-scheduler":
            payload = orchestrator_scheduler.build_dashboard_scheduler(
                conn,
                workspace_id_or_uri=workspace_id,
                limit=limit,
            )
            return status_for_payload(payload), payload
        if endpoint == "orchestration-traces":
            payload = get_dashboard_orchestration_traces(conn, workspace_id, limit=limit)
            return status_for_payload(payload), payload

    return 404, {"status": "not_found", "workspace_id": workspace_id, "endpoint": endpoint}


def dashboard_index(settings: Settings | None = None) -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "geond-dashboard",
        "read_only": True,
        "database": database_connection_info(settings or Settings()),
        "endpoints": [
            "/health",
            "/api/workspaces",
            "/api/workspaces/{workspace_id}/overview",
            "/api/workspaces/{workspace_id}/activity",
            "/api/workspaces/{workspace_id}/timeline",
            "/api/workspaces/{workspace_id}/lineage",
            "/api/workspaces/{workspace_id}/reservations",
            "/api/workspaces/{workspace_id}/handoffs",
            "/api/workspaces/{workspace_id}/sessions",
            "/api/workspaces/{workspace_id}/project",
            "/api/workspaces/{workspace_id}/code-risk",
            "/api/workspaces/{workspace_id}/changesets",
            "/api/workspaces/{workspace_id}/usage",
            "/api/workspaces/{workspace_id}/orchestration",
            "/api/workspaces/{workspace_id}/orchestration-plan",
            "/api/workspaces/{workspace_id}/orchestration-actions",
            "/api/workspaces/{workspace_id}/orchestration-action-queue",
            "/api/workspaces/{workspace_id}/orchestration-scheduler",
            "/api/workspaces/{workspace_id}/orchestration-traces",
        ],
    }


def database_connection_info(settings: Settings) -> dict[str, Any]:
    parsed = urlparse(settings.database_url)
    host = parsed.hostname or ""
    database = parsed.path.lstrip("/") or ""
    sslmode = parse_qs(parsed.query).get("sslmode", [None])[0]
    if host in {"localhost", "127.0.0.1", "::1"}:
        source = "local"
        label = "Local PostgreSQL"
    elif host.endswith(".postgres.database.azure.com"):
        source = "azure-postgresql"
        label = "Azure PostgreSQL"
    elif host:
        source = "remote-postgresql"
        label = "Remote PostgreSQL"
    else:
        source = "unknown"
        label = "Unknown database"
    return {
        "source": source,
        "label": label,
        "profile": settings.database_profile or None,
        "host": host,
        "database": database,
        "sslmode": sslmode,
    }


def dashboard_html() -> str:
    return mission_control_html()


def mission_control_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Geond Agent Activity</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #e8edf3;
      --chrome: #0f172a;
      --chrome-2: #172033;
      --surface: #ffffff;
      --surface-2: #f8fafc;
      --surface-3: #eef4f6;
      --text: #16202b;
      --muted: #637083;
      --line: #d7dde5;
      --line-strong: #bdc9d5;
      --accent: #0b6b63;
      --accent-soft: #dcefeb;
      --cyan: #00b7d6;
      --accent-2: #6d4aff;
      --warn: #9a5b00;
      --danger: #a33a34;
      --ok: #167044;
      --agent-color: var(--accent);
      --ease: cubic-bezier(0.4, 0, 0.2, 1);
      --shadow: 0 16px 42px rgba(27, 39, 53, 0.08);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system,
        BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background:
        radial-gradient(circle at 12% -10%, rgba(0, 183, 214, 0.14), transparent 30%),
        linear-gradient(180deg, #eef3f6 0, var(--bg) 280px);
      color: var(--text);
      font-size: 14px;
      letter-spacing: 0;
      height: 100vh;
      overflow: hidden;
    }
    body > header {
      height: 92px;
      display: grid;
      grid-template-columns: minmax(230px, .6fr) minmax(420px, 1.35fr) minmax(180px, .45fr);
      align-items: center;
      gap: 18px;
      padding: 0 24px;
      border-bottom: 1px solid rgba(0, 217, 255, 0.28);
      background:
        linear-gradient(90deg, var(--chrome) 0%, var(--chrome-2) 72%, #111827 100%);
      backdrop-filter: blur(10px);
      box-shadow: 0 18px 44px rgba(15, 23, 42, 0.2);
      color: #f8fafc;
      position: sticky;
      top: 0;
      z-index: 5;
    }
    .brand {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr);
      align-items: center;
      gap: 12px;
      min-width: 0;
    }
    .brand-mark {
      display: grid;
      place-items: center;
      width: 42px;
      height: 42px;
      border: 1px solid rgba(0, 217, 255, 0.38);
      border-radius: 10px;
      background: linear-gradient(135deg, rgba(0, 217, 255, .26), rgba(124, 58, 237, .38));
      box-shadow: 0 0 24px rgba(0, 217, 255, 0.24);
      font-weight: 820;
    }
    .brand-copy {
      min-width: 0;
    }
    h1 {
      margin: 0;
      font-size: 20px;
      font-weight: 760;
      line-height: 1.05;
    }
    .subtitle {
      margin-top: 5px;
      color: #a7b4c8;
      font-size: 12px;
      font-weight: 600;
    }
    .runtime {
      display: flex;
      align-items: center;
      gap: 8px;
      min-width: 220px;
      justify-content: flex-end;
    }
    .runtime .meta {
      max-width: 320px;
      color: #a7b4c8;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      margin-top: 0;
    }
    main {
      height: calc(100vh - 92px);
      padding: 14px 18px 18px;
      overflow: hidden;
    }
    .toolbar {
      display: grid;
      grid-template-columns: minmax(280px, 1fr) auto auto auto;
      gap: 10px;
      align-items: center;
      width: min(960px, 100%);
    }
    label {
      display: grid;
      gap: 4px;
      color: #a7b4c8;
      font-size: 12px;
      font-weight: 600;
    }
    input, select, button {
      height: 38px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: rgba(255, 255, 255, 0.94);
      color: var(--text);
      font: inherit;
      transition:
        border-color 180ms var(--ease),
        box-shadow 180ms var(--ease),
        transform 180ms var(--ease),
        background 180ms var(--ease);
    }
    input { padding: 0 10px; min-width: 0; }
    select { padding: 0 28px 0 10px; }
    .control-meta {
      min-height: 16px;
      color: #93a2b7;
      font-size: 11px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    button {
      padding: 0 12px;
      background: var(--accent);
      color: #fff;
      border-color: var(--accent);
      font-weight: 650;
      cursor: pointer;
      box-shadow: 0 7px 18px rgba(11, 107, 99, 0.14);
    }
    button.secondary {
      background: #fff;
      color: var(--text);
      border-color: var(--line);
      box-shadow: none;
    }
    button:hover {
      border-color: var(--line-strong);
      transform: translateY(-1px);
    }
    button:focus-visible,
    input:focus-visible,
    select:focus-visible,
    summary:focus-visible {
      outline: 2px solid var(--cyan);
      outline-offset: 2px;
    }
    .filter-strip {
      display: grid;
      grid-template-columns: repeat(5, minmax(110px, auto));
      gap: 10px;
      align-items: end;
      margin-bottom: 12px;
    }
    .filter-strip label {
      display: grid;
      gap: 4px;
      font-size: 11px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: .04em;
    }
    .filter-strip input, .filter-strip select {
      min-width: 0;
      background: var(--surface);
    }
    .status-line {
      min-height: 20px;
      margin: 0 0 10px;
      color: #526174;
      font-size: 13px;
    }
    .tabs {
      display: flex;
      gap: 8px;
      margin-bottom: 10px;
      overflow-x: auto;
      padding: 4px;
      border: 1px solid #cfd8e3;
      border-radius: 8px;
      background: rgba(15, 23, 42, 0.88);
      box-shadow: 0 12px 28px rgba(15, 23, 42, 0.12);
    }
    .tab {
      height: 34px;
      background: transparent;
      color: #cbd5e1;
      border-color: transparent;
      white-space: nowrap;
      box-shadow: none;
    }
    .tab.active {
      background: #f8fafc;
      color: #102033;
      border-color: #f8fafc;
      box-shadow: 0 0 0 1px rgba(0, 217, 255, 0.12);
    }
    .view {
      display: none;
      height: calc(100% - 62px);
      min-height: 0;
    }
    .view.active {
      display: block;
      animation: viewIn 180ms var(--ease);
    }
    .overview-shell {
      display: grid;
      grid-template-columns: 360px minmax(0, 1fr);
      gap: 14px;
      height: 100%;
      min-height: 0;
    }
    .compact-stack {
      display: grid;
      gap: 10px;
      align-content: start;
      overflow: auto;
      min-height: 0;
      padding-right: 2px;
    }
    .agent-board, .session-board {
      display: grid;
      grid-auto-flow: column;
      grid-auto-columns: minmax(360px, 1fr);
      gap: 14px;
      height: 100%;
      min-width: 0;
      overflow-x: auto;
      overflow-y: hidden;
      padding-bottom: 8px;
    }
    .session-workspace {
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      gap: 8px;
      height: 100%;
      min-height: 0;
    }
    .session-summary {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(132px, 1fr));
      gap: 8px;
    }
    .session-stat {
      min-height: 58px;
      padding: 10px 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface);
      box-shadow: 0 8px 22px rgba(15, 23, 42, 0.04);
      position: relative;
    }
    .session-stat span {
      display: block;
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
      margin-bottom: 5px;
    }
    .session-stat strong {
      font-size: 20px;
      line-height: 1;
    }
    .agent-workspace {
      display: grid;
      grid-template-rows: auto auto minmax(0, 1fr);
      gap: 8px;
      min-height: 0;
      height: 100%;
    }
    .mode-strip {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
      min-width: 0;
    }
    .mode-card {
      min-height: 58px;
      padding: 9px 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface);
      box-shadow: 0 8px 22px rgba(15, 23, 42, 0.04);
    }
    .mode-card strong,
    .mode-card span {
      display: block;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .mode-card strong {
      font-size: 13px;
      margin-bottom: 5px;
    }
    .mode-card span {
      color: var(--muted);
      font-size: 12px;
      font-weight: 600;
    }
    .agent-switchboard {
      display: flex;
      gap: 8px;
      overflow-x: auto;
      padding: 2px 0 4px;
    }
    .agent-chip {
      min-width: 150px;
      height: auto;
      display: grid;
      gap: 3px;
      justify-items: start;
      padding: 7px 10px;
      border-color: var(--line);
      background: var(--surface);
      color: var(--text);
      text-align: left;
      box-shadow: 0 6px 16px rgba(15, 23, 42, 0.04);
    }
    .agent-chip strong,
    .agent-chip span {
      max-width: 100%;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .agent-chip span {
      color: var(--muted);
      font-size: 11px;
      font-weight: 600;
    }
    .agent-chip.active {
      border-color: var(--accent);
      background: var(--accent-soft);
      box-shadow: inset 3px 0 0 var(--agent-color);
    }
    .agent-lane, .session-lane {
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      min-height: 0;
      background: rgba(255, 255, 255, 0.95);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      overflow: hidden;
      transition:
        border-color 200ms var(--ease),
        box-shadow 200ms var(--ease),
        transform 200ms var(--ease);
    }
    .agent-lane[data-active="true"] {
      border-color: color-mix(in srgb, var(--agent-color), var(--line) 60%);
      box-shadow:
        inset 3px 0 0 var(--agent-color),
        0 18px 42px rgba(27, 39, 53, 0.1);
    }
    .lane-head {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 8px;
      align-items: start;
      padding: 12px;
      border-bottom: 1px solid var(--line);
      background:
        linear-gradient(90deg, color-mix(in srgb, var(--agent-color), #fff 88%), #fff 70%),
        linear-gradient(180deg, #ffffff 0, #f8fafc 100%);
    }
    .lane-body {
      display: grid;
      gap: 10px;
      align-content: start;
      overflow: auto;
      min-height: 0;
      padding: 10px;
    }
    .collapsible {
      border-top: 1px solid var(--line);
    }
    details.collapsible {
      border: 1px solid var(--line);
      border-radius: 7px;
      background: #fff;
      overflow: hidden;
      transition: border-color 180ms var(--ease);
    }
    details.collapsible > summary {
      cursor: pointer;
      padding: 9px 10px;
      font-weight: 680;
      list-style-position: inside;
      color: #263445;
    }
    details.collapsible > .list,
    details.collapsible > .mini-list {
      margin-top: 0;
      padding: 0 10px 10px;
    }
    .mini-list {
      display: grid;
      gap: 7px;
    }
    .session-card, .message {
      border: 1px solid var(--line);
      border-radius: 7px;
      background: #fff;
      padding: 9px 10px;
      box-shadow: 0 8px 20px rgba(27, 39, 53, 0.04);
    }
    .session-card {
      display: grid;
      gap: 8px;
    }
    .session-facts {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }
    .message {
      background: var(--surface-2);
      border-color: #e4eaf0;
    }
    .message .role {
      color: var(--accent);
      font-size: 12px;
      font-weight: 700;
      margin-bottom: 4px;
    }
    .message .content {
      font-size: 12px;
      line-height: 1.45;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .agent-name {
      display: inline-flex;
      align-items: center;
      gap: 7px;
      min-width: 0;
    }
    .agent-icon {
      display: inline-grid;
      place-items: center;
      width: 24px;
      height: 24px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent);
      flex: 0 0 auto;
      font-size: 14px;
      box-shadow: 0 0 0 3px color-mix(in srgb, var(--agent-color), transparent 82%);
    }
    .project-tree {
      display: grid;
      gap: 6px;
      max-height: 240px;
      overflow: auto;
      padding: 10px;
    }
    .project-file {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr) auto;
      gap: 8px;
      align-items: start;
      padding: 8px 9px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
    }
    .project-file .icon {
      color: var(--accent-2);
      line-height: 1.25;
    }
    .project-file .path {
      font-size: 12px;
      font-weight: 650;
      overflow-wrap: anywhere;
    }
    .project-file .meta {
      margin-top: 2px;
    }
    .live-indicator {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }
    .trace-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(260px, 1fr));
      gap: 12px;
      height: 100%;
      overflow: auto;
      align-items: start;
    }
    section {
      background: rgba(255, 255, 255, 0.95);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      box-shadow: var(--shadow);
    }
    section > header {
      position: static;
      display: flex;
      height: 48px;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding: 0 14px;
      border-bottom: 1px solid var(--line);
      background: linear-gradient(180deg, #ffffff 0, #f8fafc 100%);
      color: var(--text);
    }
    section h2 {
      margin: 0;
      font-size: 14px;
      font-weight: 720;
    }
    .metrics {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      border-bottom: 1px solid var(--line);
    }
    .metric {
      padding: 13px 14px;
      border-right: 1px solid var(--line);
      min-height: 68px;
      background: #fff;
      position: relative;
    }
    .metric:nth-child(1) { --agent-color: #00b7d6; }
    .metric:nth-child(2) { --agent-color: #7c3aed; }
    .metric:nth-child(3) { --agent-color: #0b6b63; }
    .metric:nth-child(4) { --agent-color: #f59e0b; }
    .metric:nth-child(5) { --agent-color: #2563eb; }
    .metric:nth-child(6) { --agent-color: #10b981; }
    .metric::before,
    .session-stat::before {
      content: "";
      position: absolute;
      left: 0;
      top: 10px;
      bottom: 10px;
      width: 3px;
      border-radius: 0 999px 999px 0;
      background: var(--agent-color);
      opacity: .7;
    }
    .metric:last-child { border-right: 0; }
    .metric span {
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 6px;
    }
    .metric strong {
      font-size: 24px;
      line-height: 1;
    }
    .split {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 0;
      border-top: 1px solid var(--line);
    }
    .panel {
      min-height: 180px;
      padding: 14px;
    }
    .panel + .panel { border-left: 1px solid var(--line); }
    .list {
      display: grid;
      gap: 8px;
      margin-top: 10px;
    }
    .row {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
      align-items: start;
      padding: 9px 10px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      box-shadow: 0 7px 18px rgba(27, 39, 53, 0.04);
      transition:
        border-color 180ms var(--ease),
        box-shadow 180ms var(--ease),
        transform 180ms var(--ease);
    }
    .row:hover {
      border-color: var(--line-strong);
      box-shadow: 0 12px 24px rgba(27, 39, 53, 0.07);
    }
    .title {
      font-weight: 650;
      overflow-wrap: anywhere;
    }
    .meta {
      color: var(--muted);
      font-size: 12px;
      margin-top: 3px;
      overflow-wrap: anywhere;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      min-height: 22px;
      padding: 2px 8px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: var(--surface-2);
      font-size: 12px;
      white-space: nowrap;
    }
    .badge::before {
      content: "";
      width: 6px;
      height: 6px;
      margin-right: 6px;
      border-radius: 999px;
      background: currentColor;
      opacity: .62;
    }
    .agent-lane[data-active="true"] .lane-head .badge::before {
      animation: statusPulse 1.8s var(--ease) infinite;
      opacity: 1;
    }
    .badge.ok { color: var(--ok); background: #e4f5ee; border-color: #b7ddcb; }
    .badge.warn { color: var(--warn); background: #fff7df; border-color: #ead391; }
    .badge.danger { color: var(--danger); background: #fff0ed; border-color: #efb8b0; }
    .timeline {
      display: grid;
      gap: 0;
      height: 100%;
      overflow: auto;
    }
    .event {
      display: grid;
      grid-template-columns: 142px minmax(0, 1fr) auto;
      gap: 10px;
      padding: 11px 14px;
      border-bottom: 1px solid var(--line);
      position: relative;
      background: #fff;
    }
    .event::before {
      content: "";
      position: absolute;
      left: 126px;
      top: 0;
      bottom: 0;
      width: 1px;
      background: #dce4ec;
    }
    .event::after {
      content: "";
      position: absolute;
      left: 121px;
      top: 18px;
      width: 11px;
      height: 11px;
      border-radius: 999px;
      background: var(--agent-color);
      box-shadow: 0 0 0 4px #fff;
    }
    .event:last-child { border-bottom: 0; }
    .event time {
      color: var(--muted);
      font-size: 12px;
    }
    .event-detail {
      grid-column: 1 / -1;
      margin-top: 2px;
    }
    .event-detail > summary {
      cursor: pointer;
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
      list-style-position: inside;
    }
    .event-detail > .mini-list {
      margin-top: 8px;
    }
    .empty {
      padding: 18px;
      color: var(--muted);
    }
    .lineage {
      display: grid;
      grid-template-columns: 1fr 1fr;
      border-top: 1px solid var(--line);
    }
    .lineage div {
      padding: 14px;
    }
    .lineage div + div {
      border-left: 1px solid var(--line);
    }
    .full-panel {
      border-top: 1px solid var(--line);
      padding: 14px;
    }
    .agent-row {
      grid-template-columns: minmax(0, 1fr) auto auto;
    }
    @keyframes viewIn {
      from { opacity: 0; transform: translateY(4px); }
      to { opacity: 1; transform: translateY(0); }
    }
    @keyframes statusPulse {
      0%, 100% { transform: scale(1); box-shadow: 0 0 0 0 currentColor; }
      50% { transform: scale(1.15); box-shadow: 0 0 0 5px transparent; }
    }
    @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after {
        animation-duration: .01ms !important;
        scroll-behavior: auto !important;
        transition-duration: .01ms !important;
      }
    }
    @media (max-width: 980px) {
      body > header {
        height: auto;
        padding: 12px 14px;
        align-items: stretch;
        grid-template-columns: 1fr;
        gap: 10px;
      }
      .toolbar {
        grid-template-columns: minmax(220px, 1fr) 88px 88px 82px;
        width: 100%;
      }
      .toolbar button { padding: 0; }
      .runtime { justify-content: flex-start; min-width: 0; }
      main { height: calc(100vh - 220px); padding: 10px; }
      .overview-shell, .split, .lineage, .trace-grid { grid-template-columns: 1fr; }
      .filter-strip { grid-template-columns: 1fr; }
      .mode-strip { grid-template-columns: 1fr; }
      .session-summary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .panel + .panel, .lineage div + div { border-left: 0; border-top: 1px solid var(--line); }
      .event { grid-template-columns: 1fr; }
    }
    @media (max-width: 640px) {
      .toolbar { grid-template-columns: 1fr; }
      main { height: calc(100vh - 300px); }
    }
  </style>
</head>
<body>
  <header>
    <div class="brand" aria-label="Geond dashboard">
      <div class="brand-mark" aria-hidden="true">G</div>
      <div class="brand-copy">
        <h1>Geond Dashboard</h1>
        <div class="subtitle">Multi-agent coordination control plane</div>
      </div>
    </div>
    <form class="toolbar" id="controls">
      <label>
        Workspace
        <select id="workspace" name="workspace"></select>
        <span class="control-meta" id="workspace-meta">Loading workspaces</span>
      </label>
      <label>
        Window
        <select id="limit" name="limit">
          <option>50</option>
          <option selected>100</option>
          <option>250</option>
          <option>500</option>
        </select>
      </label>
      <label>
        Live
        <select id="refresh-interval" name="refresh_interval">
          <option value="0">Off</option>
          <option value="5">5s</option>
          <option value="10" selected>10s</option>
          <option value="30">30s</option>
        </select>
      </label>
      <button type="submit" title="Refresh" aria-label="Refresh">Refresh</button>
    </form>
    <div class="runtime" aria-label="Database runtime">
      <span class="badge" id="database-badge">database</span>
      <span class="meta" id="database-meta">Loading runtime</span>
    </div>
  </header>
  <main>
    <div class="status-line" id="status">Loading workspaces...</div>
    <nav class="tabs" aria-label="Dashboard views">
      <button class="tab active" type="button" data-view="mission">Mission Control</button>
      <button class="tab" type="button" data-view="sessions">Sessions</button>
      <button class="tab" type="button" data-view="handoffs">Handoffs</button>
      <button class="tab" type="button" data-view="code-risk">Code Risk</button>
      <button class="tab" type="button" data-view="changesets">Changesets</button>
      <button class="tab" type="button" data-view="orchestration">Orchestration</button>
      <button class="tab" type="button" data-view="graph">Graph</button>
      <button class="tab" type="button" data-view="usage">Usage Evidence</button>
      <button class="tab" type="button" data-view="timeline">Timeline</button>
      <button class="tab" type="button" data-view="trace">Relationships</button>
    </nav>
    <section class="view active" data-view-panel="mission">
      <div class="overview-shell">
        <aside class="compact-stack">
          <section>
            <header>
              <h2>Command Center</h2>
              <button class="secondary" id="copy-api" type="button">Copy API</button>
            </header>
            <div class="metrics" id="metrics"></div>
          </section>
          <details class="collapsible" open>
            <summary>Project Structure</summary>
            <div class="project-tree" id="project-tree"></div>
          </details>
          <details class="collapsible" open>
            <summary>Active Reservations</summary>
            <div class="list" id="reservations"></div>
          </details>
          <details class="collapsible" open>
            <summary>Open Handoffs</summary>
            <div class="list" id="handoffs"></div>
          </details>
          <details class="collapsible">
            <summary>Lineage</summary>
            <div class="lineage">
              <div><div class="meta">Lineage nodes</div><strong id="lineage-nodes">0</strong></div>
              <div><div class="meta">Lineage edges</div><strong id="lineage-edges">0</strong></div>
            </div>
          </details>
        </aside>
        <div class="agent-workspace">
          <div class="mode-strip" aria-label="Dashboard evidence model">
            <div class="mode-card">
              <strong>Agent Lanes</strong><span>live ownership and blockers</span>
            </div>
            <div class="mode-card">
              <strong>Evidence Sessions</strong><span>user prompts and agent replies</span>
            </div>
            <div class="mode-card">
              <strong>Relationships</strong><span>agents, work, files, and time</span>
            </div>
          </div>
          <div class="agent-switchboard" id="agent-switchboard"
            aria-label="Agent switchboard"></div>
          <div class="agent-board" id="agent-board"></div>
        </div>
      </div>
    </section>
    <section class="view" data-view-panel="sessions">
      <div class="session-workspace">
        <div class="session-summary" id="session-summary"></div>
        <div class="session-board" id="session-board"></div>
      </div>
    </section>
    <section class="view" data-view-panel="handoffs">
      <div class="session-workspace">
        <div class="session-summary" id="handoff-summary"></div>
        <div class="session-board" id="handoff-board"></div>
      </div>
    </section>
    <section class="view" data-view-panel="code-risk">
      <div class="session-workspace">
        <div class="session-summary" id="code-risk-summary"></div>
        <section>
          <header><h2>Hot Files</h2></header>
          <div class="panel"><div class="list" id="code-risk-files"></div></div>
        </section>
      </div>
    </section>
    <section class="view" data-view-panel="changesets">
      <div class="session-workspace">
        <div class="session-summary" id="changeset-summary"></div>
        <div class="session-board" id="changeset-board"></div>
      </div>
    </section>
    <section class="view" data-view-panel="orchestration">
      <div class="session-workspace">
        <div class="session-summary" id="orchestration-summary"></div>
        <div class="session-summary" id="orchestration-plan-summary"></div>
        <div class="session-board" id="orchestration-action-board"></div>
        <div class="session-board" id="orchestration-action-queue-board"></div>
        <div class="session-board" id="orchestration-scheduler-board"></div>
        <div class="session-board" id="orchestration-plan-board"></div>
        <div class="session-board" id="orchestration-trace-board"></div>
        <div class="session-board" id="orchestration-board"></div>
      </div>
    </section>
    <section class="view" data-view-panel="graph">
      <div class="trace-grid">
        <section>
          <header><h2>Graph Summary</h2></header>
          <div class="panel"><div class="session-summary" id="graph-summary"></div></div>
        </section>
        <section>
          <header><h2>Graph Nodes</h2></header>
          <div class="panel"><div class="list" id="graph-nodes"></div></div>
        </section>
        <section>
          <header><h2>Graph Edges</h2></header>
          <div class="panel"><div class="list" id="graph-edges"></div></div>
        </section>
      </div>
    </section>
    <section class="view" data-view-panel="usage">
      <div class="trace-grid">
        <section>
          <header><h2>Usage Summary</h2></header>
          <div class="panel">
            <div class="session-summary" id="usage-summary"></div>
          </div>
        </section>
        <section>
          <header><h2>Usage By Source</h2></header>
          <div class="panel"><div class="list" id="usage-source"></div></div>
        </section>
        <section>
          <header><h2>Usage Evidence</h2></header>
          <div class="panel"><div class="list" id="usage-evidence"></div></div>
        </section>
      </div>
    </section>
    <section class="view" data-view-panel="timeline">
      <section>
        <header>
          <h2>Activity Timeline</h2>
          <span class="badge" id="activity-count">0 events</span>
        </header>
        <div class="filter-strip" aria-label="Activity filters">
          <label>
            Kind
            <select id="activity-kind-filter">
              <option value="">All</option>
              <option value="session">Session</option>
              <option value="agent_action">Agent Action</option>
              <option value="file_reservation">File Reservation</option>
              <option value="symbol_reservation">Symbol Reservation</option>
              <option value="reservation_event">Reservation Event</option>
              <option value="handoff_summary">Handoff Summary</option>
              <option value="changeset">Changeset</option>
              <option value="benchmark_run">Benchmark Run</option>
            </select>
          </label>
          <label>
            Agent
            <input id="activity-agent-filter" type="text" placeholder="any" />
          </label>
          <label>
            Status
            <input id="activity-status-filter" type="text" placeholder="any" />
          </label>
          <button class="secondary" id="activity-filter-apply" type="button">Apply</button>
          <button class="secondary" id="activity-filter-clear" type="button">Clear</button>
        </div>
        <div class="timeline" id="timeline"></div>
      </section>
    </section>
    <section class="view" data-view-panel="trace">
      <div class="trace-grid" id="trace-grid">
        <section>
          <header><h2>Agent To Sessions</h2></header>
          <div class="panel">
            <div class="row">
              <div>
                <div class="title">Loading relationships</div>
                <div class="meta">Waiting for dashboard data.</div>
              </div>
              <span class="badge warn">pending</span>
            </div>
          </div>
        </section>
        <section>
          <header><h2>Agent To Work</h2></header>
          <div class="panel">
            <div class="row">
              <div>
                <div class="title">Loading work links</div>
                <div class="meta">Waiting for reservations and handoffs.</div>
              </div>
              <span class="badge warn">pending</span>
            </div>
          </div>
        </section>
        <section>
          <header><h2>Evidence To Timeline</h2></header>
          <div class="panel">
            <div class="row">
              <div>
                <div class="title">Loading timeline links</div>
                <div class="meta">Waiting for sessions and events.</div>
              </div>
              <span class="badge warn">pending</span>
            </div>
          </div>
        </section>
      </div>
    </section>
  </main>
  <script>
    const state = {
      workspace: "",
      limit: 100,
      autoRefreshSeconds: 10,
      refreshTimer: null,
      loading: false,
      overview: null,
      events: [],
      activityFilters: { kind: "", agent: "", status: "" },
      sessions: [],
      project: null,
      usage: null,
      handoffs: [],
      codeRisk: null,
      changesets: [],
      orchestration: null,
      orchestrationPlan: null,
      orchestrationActions: null,
      orchestrationActionQueue: null,
      orchestrationScheduler: null,
      orchestrationTraces: null,
      lineage: null,
      workspaces: [],
    };
    const qs = new URLSearchParams(location.search);
    const workspaceInput = document.querySelector("#workspace");
    const workspaceMeta = document.querySelector("#workspace-meta");
    const limitInput = document.querySelector("#limit");
    const refreshInput = document.querySelector("#refresh-interval");
    const statusLine = document.querySelector("#status");
    const metrics = document.querySelector("#metrics");
    const projectTree = document.querySelector("#project-tree");
    const reservations = document.querySelector("#reservations");
    const handoffs = document.querySelector("#handoffs");
    const databaseBadge = document.querySelector("#database-badge");
    const databaseMeta = document.querySelector("#database-meta");
    const agentSwitchboard = document.querySelector("#agent-switchboard");
    const agentBoard = document.querySelector("#agent-board");
    const sessionSummary = document.querySelector("#session-summary");
    const sessionBoard = document.querySelector("#session-board");
    const handoffSummary = document.querySelector("#handoff-summary");
    const handoffBoard = document.querySelector("#handoff-board");
    const codeRiskSummary = document.querySelector("#code-risk-summary");
    const codeRiskFiles = document.querySelector("#code-risk-files");
    const changesetSummary = document.querySelector("#changeset-summary");
    const changesetBoard = document.querySelector("#changeset-board");
    const orchestrationSummary = document.querySelector("#orchestration-summary");
    const orchestrationBoard = document.querySelector("#orchestration-board");
    const orchestrationPlanSummary = document.querySelector("#orchestration-plan-summary");
    const orchestrationPlanBoard = document.querySelector("#orchestration-plan-board");
    const orchestrationActionBoard = document.querySelector("#orchestration-action-board");
    const orchestrationActionQueueBoard = document.querySelector(
      "#orchestration-action-queue-board"
    );
    const orchestrationSchedulerBoard = document.querySelector("#orchestration-scheduler-board");
    const orchestrationTraceBoard = document.querySelector("#orchestration-trace-board");
    const graphSummary = document.querySelector("#graph-summary");
    const graphNodes = document.querySelector("#graph-nodes");
    const graphEdges = document.querySelector("#graph-edges");
    const usageSummary = document.querySelector("#usage-summary");
    const usageSource = document.querySelector("#usage-source");
    const usageEvidence = document.querySelector("#usage-evidence");
    const timeline = document.querySelector("#timeline");
    const countBadge = document.querySelector("#activity-count");
    const activityKindFilter = document.querySelector("#activity-kind-filter");
    const activityAgentFilter = document.querySelector("#activity-agent-filter");
    const activityStatusFilter = document.querySelector("#activity-status-filter");
    const activityFilterApply = document.querySelector("#activity-filter-apply");
    const activityFilterClear = document.querySelector("#activity-filter-clear");
    const lineageNodes = document.querySelector("#lineage-nodes");
    const lineageEdges = document.querySelector("#lineage-edges");
    const copyApi = document.querySelector("#copy-api");

    const requestedWorkspace = qs.get("workspace") || "";
    limitInput.value = qs.get("limit") || "100";
    refreshInput.value = qs.get("refresh") || "10";
    activityKindFilter.value = qs.get("activity_kind") || "";
    activityAgentFilter.value = qs.get("activity_agent") || "";
    activityStatusFilter.value = qs.get("activity_status") || "";

    document.querySelector("#controls").addEventListener("submit", (event) => {
      event.preventDefault();
      loadDashboard();
    });
    workspaceInput.addEventListener("change", () => loadDashboard());
    refreshInput.addEventListener("change", () => {
      state.autoRefreshSeconds = Number(refreshInput.value || 0);
      scheduleAutoRefresh();
      updateUrl();
    });
    activityFilterApply.addEventListener("click", () => loadDashboard());
    activityFilterClear.addEventListener("click", () => {
      activityKindFilter.value = "";
      activityAgentFilter.value = "";
      activityStatusFilter.value = "";
      loadDashboard();
    });
    for (const button of document.querySelectorAll(".tab")) {
      button.addEventListener("click", () => switchView(button.dataset.view));
    }
    copyApi.addEventListener("click", async () => {
      const workspace = workspaceInput.value.trim();
      if (!workspace) return;
      const url = `${location.origin}/api/workspaces/${encodeURIComponent(workspace)}/overview`;
      await navigator.clipboard.writeText(url);
      setStatus(`Copied ${url}`);
    });

    async function loadRuntimeInfo() {
      try {
        const response = await fetch("/api");
        const payload = await response.json();
        const database = payload.database || {};
        databaseBadge.textContent = database.label || "Database";
        databaseBadge.className = `badge ${database.source === "local" ? "ok" : "warn"}`;
        databaseMeta.textContent = [
          database.profile,
          database.host,
          database.database,
          database.sslmode,
        ]
          .filter(Boolean)
          .join(" | ");
        if (state.overview) renderTraceModel(state.overview, state.events, state.sessions);
      } catch (error) {
        databaseBadge.textContent = "Database unknown";
        databaseBadge.className = "badge danger";
        databaseMeta.textContent = String(error);
        if (state.overview) renderTraceModel(state.overview, state.events, state.sessions);
      }
    }

    function setStatus(message, error = false) {
      statusLine.textContent = message;
      statusLine.style.color = error ? "var(--danger)" : "var(--muted)";
    }

    function shortId(value) {
      return String(value || "").slice(0, 8);
    }

    function formatNumber(value) {
      return Number(value || 0).toLocaleString();
    }

    function formatCost(value) {
      if (value === null || value === undefined) return "n/a";
      return `$${Number(value || 0).toFixed(4)}`;
    }

    function formatShare(value) {
      if (value === null || value === undefined) return "n/a";
      return `${Math.round(Number(value) * 100)}%`;
    }

    function agentIcon(name) {
      const key = String(name || "system").toLowerCase();
      if (key.includes("copilot") || key.includes("vscode")) return "🤖";
      if (key.includes("codex")) return "⌘";
      if (key.includes("claude")) return "◆";
      if (key.includes("system")) return "◎";
      return "✦";
    }

    function agentColor(name) {
      const key = String(name || "system").toLowerCase();
      if (key.includes("copilot") || key.includes("vscode")) return "#7c3aed";
      if (key.includes("claude")) return "#00b7d6";
      if (key.includes("qa")) return "#10b981";
      if (key.includes("pm") || key.includes("doc")) return "#f59e0b";
      if (key.includes("codex")) return "#0b6b63";
      if (key.includes("system")) return "#64748b";
      return "#2563eb";
    }

    function workspaceSummary(workspace) {
      return [
        `${workspace.session_count || 0} sessions`,
        `${workspace.message_count || 0} messages`,
        (workspace.agents || []).join(" + ") || "no agents",
      ].join(" | ");
    }

    async function loadWorkspaceOptions() {
      const response = await fetch("/api/workspaces?limit=250");
      const payload = await response.json();
      state.workspaces = payload.workspaces || [];
      workspaceInput.replaceChildren();
      if (!state.workspaces.length) {
        const option = document.createElement("option");
        option.value = "";
        option.textContent = "No workspaces found";
        workspaceInput.append(option);
        workspaceMeta.textContent = "Run an import or record a changeset first.";
        return;
      }
      for (const workspace of state.workspaces) {
        const option = document.createElement("option");
        option.value = workspace.workspace_id;
        option.textContent = [
          `${agentIcon((workspace.agents || [])[0])} ${workspace.workspace_name}`,
          `(${shortId(workspace.workspace_id)})`,
        ].join(" ");
        option.dataset.summary = workspaceSummary(workspace);
        option.title = [workspace.workspace_uri, ...(workspace.aliases || []).map(
          (alias) => alias.alias_uri
        )].filter(Boolean).join("\\n");
        workspaceInput.append(option);
      }
      const selected = state.workspaces.find((workspace) => (
        workspace.workspace_id === requestedWorkspace ||
        workspace.workspace_uri === requestedWorkspace ||
        (workspace.aliases || []).some((alias) => alias.alias_uri === requestedWorkspace)
      )) || state.workspaces.find((workspace) => (workspace.session_count || 0) > 0)
        || state.workspaces[0];
      workspaceInput.value = selected.workspace_id;
      updateWorkspaceMeta();
    }

    function updateWorkspaceMeta() {
      const option = workspaceInput.selectedOptions[0];
      workspaceMeta.textContent = option?.dataset.summary || "";
    }

    function scheduleAutoRefresh() {
      if (state.refreshTimer) clearInterval(state.refreshTimer);
      state.refreshTimer = null;
      if (!state.autoRefreshSeconds) return;
      state.refreshTimer = setInterval(() => {
        if (!document.hidden) loadDashboard({ silent: true });
      }, state.autoRefreshSeconds * 1000);
    }

    function readActivityFilters() {
      return {
        kind: activityKindFilter.value,
        agent: activityAgentFilter.value.trim(),
        status: activityStatusFilter.value.trim(),
      };
    }

    function setOptionalQueryParam(params, key, value) {
      if (value) {
        params.set(key, value);
      } else {
        params.delete(key);
      }
    }

    function updateUrl() {
      const workspace = workspaceInput.value.trim();
      if (!workspace) return;
      const url = new URL(location.href);
      const filters = state.activityFilters || readActivityFilters();
      url.searchParams.set("workspace", workspace);
      url.searchParams.set("limit", limitInput.value);
      url.searchParams.set("refresh", refreshInput.value);
      setOptionalQueryParam(url.searchParams, "activity_kind", filters.kind);
      setOptionalQueryParam(url.searchParams, "activity_agent", filters.agent);
      setOptionalQueryParam(url.searchParams, "activity_status", filters.status);
      history.replaceState(null, "", url);
    }

    function switchView(view) {
      for (const button of document.querySelectorAll(".tab")) {
        button.classList.toggle("active", button.dataset.view === view);
      }
      for (const panel of document.querySelectorAll("[data-view-panel]")) {
        panel.classList.toggle("active", panel.dataset.viewPanel === view);
      }
    }

    function badgeClass(status) {
      if (["active", "ok", "open", "recorded", "created"].includes(status)) return "ok";
      if (["expired", "warning", "medium"].includes(status)) return "warn";
      if (["released", "closed", "error", "failed", "high", "critical"].includes(status)) {
        return "danger";
      }
      if (["low"].includes(status)) return "ok";
      return "";
    }

    function row(title, meta, status, label) {
      const item = document.createElement("div");
      item.className = "row";
      item.innerHTML = `
        <div><div class="title"></div><div class="meta"></div></div>
        <span class="badge ${badgeClass(status)}"></span>`;
      item.querySelector(".title").textContent = title || "Untitled";
      item.querySelector(".meta").textContent = meta || "";
      item.querySelector(".badge").textContent = label || status || "unknown";
      return item;
    }

    function renderList(target, items, emptyText, mapper) {
      target.replaceChildren();
      if (!items.length) {
        const empty = document.createElement("div");
        empty.className = "empty";
        empty.textContent = emptyText;
        target.append(empty);
        return;
      }
      for (const item of items) target.append(mapper(item));
    }

    function compactDetailValue(value) {
      if (value === null || value === undefined || value === "") return "n/a";
      if (typeof value === "object") {
        try {
          return JSON.stringify(value).slice(0, 280);
        } catch {
          return "[object]";
        }
      }
      return String(value).slice(0, 280);
    }

    function eventDetailRows(event) {
      const rows = [
        ["Event ID", event.id],
        ["Artifact", [event.artifact_type, event.artifact_id].filter(Boolean).join(":")],
        ["Agent", event.agent_name],
        ["Occurred", event.occurred_at],
      ].filter(([, value]) => value);
      for (const [key, value] of Object.entries(event.metadata || {}).slice(0, 8)) {
        rows.push([`metadata.${key}`, compactDetailValue(value)]);
      }
      return rows;
    }

    function eventArtifactId(event) {
      return String(event.artifact_id || event.id || "");
    }

    function matchingLineageNodes(event) {
      const artifactId = eventArtifactId(event);
      if (!artifactId) return [];
      return (state.lineage?.nodes || []).filter((node) => (
        node.raw_id === artifactId ||
        node.id === `${event.kind}:${artifactId}` ||
        node.id === `${event.artifact_type}:${artifactId}`
      ));
    }

    function relatedReviewRows(event) {
      const artifactId = eventArtifactId(event);
      if (!artifactId) return [];
      const rows = [];
      const session = state.sessions.find((item) => (
        item.session_id === artifactId || item.external_id === artifactId
      ));
      if (session) {
        rows.push(["Related Session", sessionEvidenceMeta(session), "ok", "session"]);
      }
      const changeset = state.changesets.find((item) => (
        item.changeset_id === artifactId || item.git_commit === artifactId
      ));
      if (changeset) {
        rows.push(["Related Changeset", changesetMeta(changeset), "ok", "changeset"]);
      }
      const handoff = state.handoffs.find((item) => item.handoff_id === artifactId);
      if (handoff) {
        rows.push(["Related Handoff", handoffMeta(handoff), handoff.status || "ok", "handoff"]);
      }
      for (const node of matchingLineageNodes(event).slice(0, 3)) {
        rows.push([
          "Related Graph Node",
          [node.kind, node.title || node.raw_id, node.status].filter(Boolean).join(" | "),
          node.status || "ok",
          "graph",
        ]);
      }
      if (!rows.length) return [];
      return [["Related Review Context", `${rows.length} matches`, "ok", "links"], ...rows];
    }

    function appendEventDetails(item, event) {
      const details = document.createElement("details");
      details.className = "event-detail";
      details.innerHTML = `<summary>Details</summary><div class="mini-list"></div>`;
      const list = details.querySelector(".mini-list");
      const rows = [...eventDetailRows(event), ...relatedReviewRows(event)];
      if (!rows.length) {
        const empty = document.createElement("div");
        empty.className = "empty";
        empty.textContent = "No event details.";
        list.append(empty);
      } else {
        list.replaceChildren(
          ...rows.map(([title, value, status, label]) => (
            row(title, value, status || "ok", label || "detail")
          ))
        );
      }
      item.append(details);
    }

    function fileActivityIcon(file) {
      if (file.active_file_claims) return "●";
      if (file.changeset_count) return "◆";
      return "◇";
    }

    function fileActivityMeta(file) {
      const parts = [];
      if (file.changeset_count) parts.push(`${file.changeset_count} changesets`);
      if (file.symbol_count) parts.push(`${file.symbol_count} symbols`);
      if (file.active_agents?.length) parts.push(`claimed by ${file.active_agents.join(", ")}`);
      if (file.latest_changed_at) parts.push(new Date(file.latest_changed_at).toLocaleString());
      return parts.join(" | ") || "tracked file";
    }

    function renderProjectTree(project) {
      const files = project?.files || [];
      projectTree.replaceChildren();
      if (!files.length) {
        const empty = document.createElement("div");
        empty.className = "empty";
        empty.textContent = "No indexed or changed files for this workspace.";
        projectTree.append(empty);
        return;
      }
      for (const file of files.slice(0, 80)) {
        const item = document.createElement("div");
        item.className = "project-file";
        item.innerHTML = `
          <span class="icon"></span>
          <div><div class="path"></div><div class="meta"></div></div>
          <span class="badge ${badgeClass(file.status)}"></span>`;
        item.querySelector(".icon").textContent = fileActivityIcon(file);
        item.querySelector(".path").textContent = file.file_path;
        item.querySelector(".meta").textContent = fileActivityMeta(file);
        item.querySelector(".badge").textContent = file.status;
        projectTree.append(item);
      }
    }

    function renderMetrics(counts, agentLaneCount) {
      const entries = [
        ["Agent Lanes", agentLaneCount],
        ["Open Handoffs", counts.open_handoffs],
        ["File Claims", counts.active_file_reservations],
        ["Symbol Claims", counts.active_symbol_reservations],
        ["Changesets", counts.changesets],
        ["Benchmarks", counts.benchmark_runs],
        ["Sessions", counts.sessions],
        ["Actions", counts.agent_actions],
      ];
      metrics.replaceChildren(...entries.map(([label, value]) => {
        const item = document.createElement("div");
        item.className = "metric";
        item.innerHTML = `<span></span><strong></strong>`;
        item.querySelector("span").textContent = label;
        item.querySelector("strong").textContent = value ?? 0;
        return item;
      }));
    }

    function renderStatCards(target, entries) {
      target.replaceChildren(...entries.map(([label, value]) => {
        const item = document.createElement("div");
        item.className = "session-stat";
        item.innerHTML = `<span></span><strong></strong>`;
        item.querySelector("span").textContent = label;
        item.querySelector("strong").textContent = value;
        return item;
      }));
    }

    function renderUsageBoard(payload) {
      const usage = payload?.usage || {};
      const totals = usage.totals || {};
      const quality = usage.data_quality || {};
      const evidence = payload?.evidence || {};
      const linked = payload?.usage_vs_evidence || {};
      renderStatCards(usageSummary, [
        ["Events", formatNumber(totals.event_count)],
        ["Tokens", formatNumber(totals.total_tokens)],
        ["Cost", formatCost(totals.estimated_cost_usd)],
        ["Exact", formatShare(quality.exact_token_share)],
        ["Estimated", formatShare(quality.estimated_token_share)],
        ["Changesets", formatNumber(evidence.changesets)],
        ["Tested Handoffs", formatNumber(evidence.tested_handoffs)],
        ["User Prompts", formatNumber(evidence.user_prompts)],
      ]);
      renderList(
        usageSource,
        usage.by_source || [],
        "No usage events recorded.",
        (item) => row(
          item.source || "unknown source",
          [
            `${formatNumber(item.total_tokens)} tokens`,
            `${formatNumber(item.event_count)} events`,
            `cost ${formatCost(item.estimated_cost_usd)}`,
          ].join(" | "),
          item.estimated_event_count ? "warning" : "ok",
          item.estimated_event_count ? "estimated" : "exact"
        )
      );
      const evidenceRows = [
        row(
          "Conversation Evidence",
          [
            `${formatNumber(evidence.sessions)} sessions`,
            `${formatNumber(evidence.messages)} messages`,
            `${formatNumber(evidence.user_prompts)} user prompts`,
            `${formatNumber(evidence.assistant_replies)} agent replies`,
          ].join(" | "),
          evidence.user_prompts || evidence.assistant_replies ? "ok" : "warning",
          "conversation"
        ),
        row(
          "Work Evidence",
          [
            `${formatNumber(evidence.changesets)} changesets`,
            `${formatNumber(evidence.handoffs)} handoffs`,
            `${formatNumber(evidence.tested_handoffs)} tested`,
            `${formatNumber(evidence.active_reservations)} claims`,
          ].join(" | "),
          linked.has_output_evidence ? "ok" : "warning",
          "work"
        ),
        row(
          "Validation Evidence",
          [
            `${formatNumber(evidence.benchmark_runs)} benchmarks`,
            `${formatNumber(evidence.agent_actions)} agent actions`,
          ].join(" | "),
          evidence.benchmark_runs || evidence.agent_actions ? "ok" : "warning",
          "validation"
        ),
        row(
          "Usage To Changesets",
          [
            `${formatNumber(totals.total_tokens)} tokens`,
            `${formatNumber(evidence.changesets)} changesets`,
          ].join(" | "),
          linked.has_output_evidence ? "ok" : "warning",
          linked.tokens_per_changeset === null ? "n/a" : `${linked.tokens_per_changeset} tok/change`
        ),
        row(
          "Usage To Tested Handoffs",
          [
            `${formatNumber(evidence.tested_handoffs)} tested handoffs`,
            `${formatNumber(evidence.handoffs)} handoffs`,
          ].join(" | "),
          evidence.tested_handoffs ? "ok" : "warning",
          linked.tokens_per_tested_handoff === null
            ? "n/a"
            : `${linked.tokens_per_tested_handoff} tok/test`
        ),
        row(
          "Review Hint",
          linked.review_hint || "No usage signal yet.",
          linked.has_output_evidence ? "ok" : "warning",
          "signal"
        ),
      ];
      usageEvidence.replaceChildren(...evidenceRows);
    }

    function renderTimeline(events) {
      timeline.replaceChildren();
      const filters = state.activityFilters || {};
      const filtered = filters.kind || filters.agent || filters.status;
      countBadge.textContent = filtered
        ? `${events.length} filtered events`
        : `${events.length} events`;
      if (!events.length) {
        const empty = document.createElement("div");
        empty.className = "empty";
        empty.textContent = "No activity yet.";
        timeline.append(empty);
        return;
      }
      for (const event of events) {
        const item = document.createElement("div");
        item.className = "event";
        item.style.setProperty("--agent-color", agentColor(event.agent_name));
        const when = event.occurred_at ? new Date(event.occurred_at).toLocaleString() : "";
        item.innerHTML = `
          <time></time>
          <div><div class="title"></div><div class="meta"></div></div>
          <span class="badge ${badgeClass(event.status)}"></span>`;
        item.querySelector("time").textContent = when;
        item.querySelector(".title").textContent = event.title || event.kind;
        item.querySelector(".meta").textContent = [
          event.kind,
          event.agent_name,
          event.artifact_type
        ].filter(Boolean).join(" | ");
        item.querySelector(".badge").textContent = event.status || event.kind;
        appendEventDetails(item, event);
        timeline.append(item);
      }
    }

    function agentKey(name) {
      return name || "system";
    }

    function readableMessages(messages) {
      const seen = new Set();
      return (messages || []).filter((message) => {
        const role = String(message.role || "").toLowerCase();
        const content = String(message.content || "").trim();
        if (!content || role === "metadata") return false;
        if (content.startsWith("toolInvocationSerialized")) return false;
        if (content.startsWith("thinking ")) return false;
        if (content.includes("toolInvocationSerialized")) return false;
        if (/^\\d+(\\s+\\d+){3,}$/.test(content)) return false;
        const contextPrefixes = [
          "<attachments>",
          "<context>",
          "<environment_info>",
          "<workspace_info>",
          "<todoList>",
          "<reminderInstructions>",
        ];
        if (contextPrefixes.some((prefix) => content.startsWith(prefix))) {
          return false;
        }
        if (content.includes("The following browser pages are currently shared with you")) {
          return false;
        }
        const fingerprint = content.replace(/\\s+/g, " ").slice(0, 500);
        if (seen.has(fingerprint)) return false;
        seen.add(fingerprint);
        return true;
      });
    }

    function messageRole(message) {
      const role = String(message.role || "message");
      if (role === "user" || role === "human") return "User Prompt";
      if (role === "assistant") return "Agent Reply";
      if (role === "metadata_or_text") return "Captured Prompt";
      if (role === "assistant_or_tool") return "Tool/Trace";
      if (role === "metadata") return "Metadata";
      if (role === "system") return "System";
      return role;
    }

    function roleCounts(session) {
      return session.role_counts || {
        user: session.user_message_count || 0,
        agent: session.assistant_message_count || 0,
        captured: session.captured_prompt_count || 0,
        technical: session.technical_message_count || 0,
      };
    }

    function sessionConversationState(session) {
      if (sessionReadableCount(session) > 0) return { label: "readable", status: "ok" };
      if (session.conversation_signal === "empty") return { label: "empty", status: "warning" };
      if (session.conversation_signal === "unreadable") {
        return { label: "unreadable", status: "warning" };
      }
      return { label: "technical", status: "warning" };
    }

    function sessionEvidenceMeta(session) {
      const counts = roleCounts(session);
      return [
        `${session.message_count || 0} stored`,
        `${counts.user || 0} user`,
        `${counts.agent || 0} agent`,
        `${counts.captured || 0} captured`,
        `${sessionReadableCount(session)} readable`,
        session.latest_message_at
          ? new Date(session.latest_message_at).toLocaleString()
          : "no time",
      ].join(" | ");
    }

    function sessionEvidenceRow(session) {
      const signal = sessionConversationState(session);
      return row(
        session.title || session.external_id || shortId(session.session_id),
        sessionEvidenceMeta(session),
        signal.status,
        signal.label
      );
    }

    function sessionReadableMessages(session) {
      if (Array.isArray(session.readable_messages) && session.readable_messages.length) {
        return session.readable_messages;
      }
      return readableMessages(session.messages);
    }

    function sessionReadableCount(session) {
      if (Number.isFinite(session.readable_excerpt_count)) {
        return session.readable_excerpt_count;
      }
      return sessionReadableMessages(session).length;
    }

    function sessionMetadata(session) {
      const metadata = session.metadata || {};
      return metadata.metadata || metadata;
    }

    function eventAgentName(event) {
      return agentKey(event.agent_name);
    }

    function sessionCard(session) {
      const allReadableMessages = sessionReadableMessages(session);
      const visibleMessages = allReadableMessages.slice(-6);
      const metadata = sessionMetadata(session);
      const counts = roleCounts(session);
      const signal = sessionConversationState(session);
      const card = document.createElement("article");
      card.className = "session-card";
      card.innerHTML = `
        <div>
          <div class="title"></div>
          <div class="meta"></div>
        </div>
        <div class="session-facts"></div>
        <details class="collapsible" open>
          <summary>Recent messages</summary>
          <div class="mini-list"></div>
        </details>`;
      card.querySelector(".title").textContent = session.title || session.external_id;
      card.querySelector(".meta").textContent = [
        session.source,
        `${session.message_count || 0} messages`,
        session.external_id
      ].filter(Boolean).join(" | ");
      const facts = [
        ["state", signal.label, signal.status],
        ["stored", `${session.message_count || 0}`],
        ["readable", `${sessionReadableCount(session)}`],
        ["user", `${counts.user || 0}`],
        ["agent", `${counts.agent || 0}`],
        ["captured", `${counts.captured || 0}`],
        ["technical", `${counts.technical || 0}`],
      ];
      if (metadata.has_editing_context) facts.push(["editing", "yes"]);
      card.querySelector(".session-facts").replaceChildren(
        ...facts.map(([label, value, status]) => {
          const item = document.createElement("span");
          item.className = `badge ${badgeClass(status)}`;
          item.textContent = `${label}: ${value}`;
          return item;
        })
      );
      const messages = card.querySelector(".mini-list");
      if (!visibleMessages.length) {
        const empty = document.createElement("div");
        empty.className = "empty";
        const inspected = session.inspected_message_count || (session.messages || []).length;
        empty.textContent = (session.messages || []).length
          ? `Technical-only recent window (${inspected} inspected).`
          : "No stored messages.";
        messages.append(empty);
        return card;
      }
      for (const message of visibleMessages) {
        const item = document.createElement("div");
        item.className = "message";
        item.innerHTML = `<div class="role"></div><div class="content"></div>`;
        item.querySelector(".role").textContent = `${messageRole(message)} #${message.ordinal}`;
        item.querySelector(".content").textContent = message.content || "";
        messages.append(item);
      }
      return card;
    }

    function renderSessionSummary(sessions) {
      const totalMessages = sessions.reduce(
        (total, session) => total + (session.message_count || 0),
        0
      );
      const readableTotal = sessions.reduce(
        (total, session) => total + sessionReadableCount(session),
        0
      );
      const roleTotals = sessions.reduce((totals, session) => {
        const counts = roleCounts(session);
        totals.user += counts.user || 0;
        totals.agent += counts.agent || 0;
        totals.captured += counts.captured || 0;
        totals.technical += counts.technical || 0;
        return totals;
      }, { user: 0, agent: 0, captured: 0, technical: 0 });
      const sources = new Set(sessions.map((session) => session.source).filter(Boolean));
      const entries = [
        ["Sessions", sessions.length],
        ["Messages", totalMessages],
        ["Readable", readableTotal],
        ["User Prompts", roleTotals.user],
        ["Agent Replies", roleTotals.agent],
        ["Captured Text", roleTotals.captured],
        ["Technical", roleTotals.technical],
      ];
      sessionSummary.replaceChildren(
        ...entries.map(([label, value]) => {
          const item = document.createElement("div");
          item.className = "session-stat";
          item.innerHTML = `<span></span><strong></strong>`;
          item.querySelector("span").textContent = label;
          item.querySelector("strong").textContent = value;
          return item;
        })
      );
    }

    function collectAgentKeys(events, sessions, overview) {
      const names = new Set();
      for (const event of events) {
        if (event.agent_name) names.add(eventAgentName(event));
      }
      for (const session of sessions) names.add(agentKey(session.agent_name));
      for (const item of [
        ...(overview.active_reservations?.files || []),
        ...(overview.active_reservations?.symbols || []),
      ]) names.add(agentKey(item.agent_name));
      for (const handoff of overview.open_handoffs || []) {
        names.add(agentKey(handoff.from_agent_name));
      }
      return [...names].filter(Boolean);
    }

    function laneShell(name, summary) {
      const lane = document.createElement("section");
      lane.className = "agent-lane";
      lane.dataset.agentKey = agentKey(name);
      lane.style.setProperty("--agent-color", agentColor(name));
      lane.innerHTML = `
        <div class="lane-head">
          <div>
            <h2 class="agent-name">
              <span class="agent-icon"></span><span class="agent-label"></span>
            </h2>
            <div class="meta"></div>
          </div>
          <span class="badge"></span>
        </div>
        <div class="lane-body"></div>`;
      lane.querySelector(".agent-icon").textContent = agentIcon(name);
      lane.querySelector(".agent-label").textContent = name;
      lane.querySelector(".meta").textContent = summary || "";
      lane.querySelector(".badge").textContent = "agent";
      return lane;
    }

    function renderAgentSwitchboard(ordered) {
      agentSwitchboard.replaceChildren();
      if (!ordered.length) {
        const chip = document.createElement("button");
        chip.className = "agent-chip";
        chip.type = "button";
        chip.innerHTML = `<strong>No agents</strong><span>Waiting for activity</span>`;
        agentSwitchboard.append(chip);
        return;
      }
      ordered.forEach(([name, laneData], index) => {
        const chip = document.createElement("button");
        chip.className = "agent-chip";
        chip.type = "button";
        chip.dataset.agentKey = agentKey(name);
        chip.style.setProperty("--agent-color", agentColor(name));
        chip.setAttribute("aria-label", `Show ${name} lane`);
        if (index === 0) chip.classList.add("active");
        const activeWork = laneData.reservations.length + laneData.handoffs.length;
        const readable = laneData.sessions.reduce(
          (total, session) => total + sessionReadableCount(session),
          0
        );
        chip.innerHTML = `
          <strong class="agent-name">
            <span class="agent-icon"></span><span></span>
          </strong>
          <span></span>`;
        chip.querySelector(".agent-icon").textContent = agentIcon(name);
        chip.querySelector("strong span:last-child").textContent = name;
        chip.querySelector("strong + span").textContent = [
          `${laneData.events.length} events`,
          `${laneData.sessions.length} sessions`,
          readable ? `${readable} readable` : "no readable",
          activeWork ? `${activeWork} active` : "idle",
        ].join(" | ");
        chip.addEventListener("click", () => {
          for (const item of agentSwitchboard.querySelectorAll(".agent-chip")) {
            item.classList.toggle("active", item === chip);
          }
          agentBoard
            .querySelector(`[data-agent-key="${CSS.escape(agentKey(name))}"]`)
            ?.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "start" });
        });
        agentSwitchboard.append(chip);
      });
    }

    function appendDetails(target, title, items, emptyText, mapper, open = false) {
      const details = document.createElement("details");
      details.className = "collapsible";
      details.open = open;
      details.innerHTML = `<summary></summary><div class="mini-list"></div>`;
      details.querySelector("summary").textContent = `${title} (${items.length})`;
      const list = details.querySelector(".mini-list");
      if (!items.length) {
        const empty = document.createElement("div");
        empty.className = "empty";
        empty.textContent = emptyText;
        list.append(empty);
      } else {
        for (const item of items) list.append(mapper(item));
      }
      target.append(details);
    }

    function renderAgentBoard(events, sessions, overview) {
      const lanes = new Map();
      const ensure = (name) => {
        const key = agentKey(name);
        if (!lanes.has(key)) {
          lanes.set(key, { events: [], sessions: [], reservations: [], handoffs: [] });
        }
        return lanes.get(key);
      };
      for (const event of events) {
        if (event.agent_name) ensure(eventAgentName(event)).events.push(event);
      }
      for (const session of sessions) ensure(session.agent_name).sessions.push(session);
      for (const item of [
        ...(overview.active_reservations?.files || []),
        ...(overview.active_reservations?.symbols || []),
      ]) {
        ensure(item.agent_name).reservations.push(item);
      }
      for (const handoff of overview.open_handoffs || []) {
        ensure(handoff.from_agent_name).handoffs.push(handoff);
      }

      agentBoard.replaceChildren();
      const ordered = [...lanes.entries()].sort((left, right) => {
        const rightTime = right[1].events[0]?.occurred_at || "";
        const leftTime = left[1].events[0]?.occurred_at || "";
        return rightTime.localeCompare(leftTime);
      });
      renderAgentSwitchboard(ordered);
      if (!ordered.length) {
        const lane = laneShell("No agents", "No activity recorded yet.");
        agentBoard.append(lane);
        return;
      }
      for (const [name, laneData] of ordered) {
        const activeWork = laneData.reservations.length + laneData.handoffs.length;
        const lane = laneShell(
          name,
          `${laneData.sessions.length} sessions | ${laneData.events.length} events`
        );
        lane.dataset.active = String(activeWork > 0);
        const body = lane.querySelector(".lane-body");
        appendDetails(
          body,
          "Active work",
          [...laneData.reservations, ...laneData.handoffs],
          "No active reservations or handoffs.",
          (item) => row(
            item.file_path || item.qualified_name || item.symbol || item.summary,
            item.purpose || item.status || item.agent_name,
            item.status || "active"
          ),
          true
        );
        appendDetails(
          body,
          "Evidence sessions",
          laneData.sessions,
          "No sessions for this agent.",
          sessionEvidenceRow,
          true
        );
        appendDetails(
          body,
          "Recent events",
          laneData.events.slice(0, 12),
          "No recent events.",
          (event) => row(event.title || event.kind, event.kind, event.status),
          false
        );
        agentBoard.append(lane);
      }
    }

    function renderSessionBoard(sessions) {
      renderSessionSummary(sessions);
      sessionBoard.replaceChildren();
      if (!sessions.length) {
        const lane = document.createElement("section");
        lane.className = "session-lane";
        lane.innerHTML = `
          <div class="lane-head"><h2>No sessions</h2></div>
          <div class="lane-body"><div class="empty">No stored sessions.</div></div>`;
        sessionBoard.append(lane);
        return;
      }
      for (const session of sessions) {
        const lane = document.createElement("section");
        lane.className = "session-lane";
        lane.innerHTML = `
          <div class="lane-head">
            <div>
              <h2 class="agent-name">
                <span class="agent-icon"></span><span class="agent-label"></span>
              </h2>
              <div class="meta"></div>
            </div>
            <span class="badge"></span>
          </div>
          <div class="lane-body"></div>`;
        const sessionAgent = session.agent_name || session.source;
        lane.querySelector(".agent-icon").textContent = agentIcon(sessionAgent);
        lane.querySelector(".agent-label").textContent = sessionAgent;
        lane.querySelector(".meta").textContent = sessionEvidenceMeta(session);
        lane.querySelector(".badge").textContent = session.source;
        lane.querySelector(".lane-body").append(sessionCard(session));
        sessionBoard.append(lane);
      }
    }

    function handoffTemplate(handoff) {
      const metadata = handoff.metadata || {};
      return metadata.handoff_template || {};
    }

    function handoffMeta(handoff) {
      const template = handoffTemplate(handoff);
      const parts = [
        `${handoff.from_agent_name || "agent"} -> ${handoff.to_agent_name || "unassigned"}`,
        handoff.created_at ? new Date(handoff.created_at).toLocaleString() : null,
      ];
      if ((template.tested_commands || []).length) {
        parts.push(`${template.tested_commands.length} tested`);
      }
      if ((template.remaining_risks || []).length) {
        parts.push(`${template.remaining_risks.length} risks`);
      }
      if ((handoff.blocked_on || []).length) {
        parts.push(`${handoff.blocked_on.length} blockers`);
      }
      return parts.filter(Boolean).join(" | ");
    }

    function handoffCard(handoff) {
      const template = handoffTemplate(handoff);
      const card = document.createElement("article");
      card.className = "session-card";
      card.innerHTML = `
        <div>
          <div class="title"></div>
          <div class="meta"></div>
        </div>
        <div class="session-facts"></div>
        <div class="mini-list"></div>`;
      card.querySelector(".title").textContent = handoff.summary || "Handoff";
      card.querySelector(".meta").textContent = handoffMeta(handoff);
      const facts = [
        ["status", handoff.status || "unknown", handoff.status || "warning"],
        ["to", handoff.to_agent_name || "unassigned"],
      ];
      if (template.next_action) facts.push(["next", template.next_action]);
      card.querySelector(".session-facts").replaceChildren(
        ...facts.map(([label, value, status]) => {
          const item = document.createElement("span");
          item.className = `badge ${badgeClass(status)}`;
          item.textContent = `${label}: ${value}`;
          return item;
        })
      );
      const list = card.querySelector(".mini-list");
      const rows = [
        ...((handoff.next_steps || []).map((item) => ["Next step", item, "ok"])),
        ...((handoff.blocked_on || []).map((item) => ["Blocked on", item, "warning"])),
        ...((template.remaining_risks || []).map((item) => ["Risk", item, "warning"])),
        ...((template.tested_commands || []).map((item) => ["Tested", item, "ok"])),
      ];
      if (!rows.length) {
        const empty = document.createElement("div");
        empty.className = "empty";
        empty.textContent = "No structured handoff details.";
        list.append(empty);
        return card;
      }
      for (const [title, meta, status] of rows) {
        list.append(row(title, meta, status, title));
      }
      return card;
    }

    function renderHandoffBoard(payload) {
      const items = payload?.handoffs || [];
      const statusCounts = items.reduce((counts, handoff) => {
        const status = handoff.status || "unknown";
        counts[status] = (counts[status] || 0) + 1;
        return counts;
      }, {});
      const tested = items.filter(
        (handoff) => (handoffTemplate(handoff).tested_commands || []).length
      ).length;
      const blocked = items.filter((handoff) => (handoff.blocked_on || []).length).length;
      const agents = new Set(
        items.flatMap((handoff) => [handoff.from_agent_name, handoff.to_agent_name]).filter(Boolean)
      );
      renderStatCards(handoffSummary, [
        ["Handoffs", formatNumber(items.length)],
        ["Open", formatNumber(statusCounts.open)],
        ["Closed", formatNumber(statusCounts.closed)],
        ["Tested", formatNumber(tested)],
        ["Blocked", formatNumber(blocked)],
        ["Agents", formatNumber(agents.size)],
      ]);
      handoffBoard.replaceChildren();
      if (!items.length) {
        const lane = document.createElement("section");
        lane.className = "session-lane";
        lane.innerHTML = `
          <div class="lane-head"><h2>No handoffs</h2></div>
          <div class="lane-body"><div class="empty">No handoff summaries recorded.</div></div>`;
        handoffBoard.append(lane);
        return;
      }
      const grouped = new Map();
      for (const handoff of items) {
        const key = agentKey(handoff.from_agent_name);
        if (!grouped.has(key)) grouped.set(key, []);
        grouped.get(key).push(handoff);
      }
      for (const [agent, handoffItems] of grouped.entries()) {
        const lane = laneShell(agent, `${handoffItems.length} handoffs`);
        const body = lane.querySelector(".lane-body");
        for (const status of ["open", "closed", "blocked", "unknown"]) {
          const matching = handoffItems.filter(
            (handoff) => (handoff.status || "unknown") === status
          );
          if (matching.length) {
            appendDetails(
              body,
              `${status.charAt(0).toUpperCase()}${status.slice(1)}`,
              matching,
              "No handoffs in this state.",
              handoffCard,
              status === "open"
            );
          }
        }
        handoffBoard.append(lane);
      }
    }

    function renderCodeRiskBoard(payload) {
      const summary = payload?.summary || {};
      const files = payload?.files || [];
      renderStatCards(codeRiskSummary, [
        ["Files", formatNumber(summary.total_files)],
        ["High", formatNumber(summary.high)],
        ["Medium", formatNumber(summary.medium)],
        ["Low", formatNumber(summary.low)],
        ["Active Claims", formatNumber(summary.active_claims)],
      ]);
      renderList(
        codeRiskFiles,
        files,
        "No code-risk signals for this workspace.",
        codeRiskCard
      );
    }

    function codeRiskCard(item) {
      const card = document.createElement("article");
      card.className = "session-card";
      card.innerHTML = `
        <div>
          <div class="title"></div>
          <div class="meta"></div>
        </div>
        <div class="session-facts"></div>
        <div class="mini-list"></div>`;
      card.querySelector(".title").textContent = item.file_path;
      card.querySelector(".meta").textContent = [
        `score ${formatNumber(item.risk_score)}`,
        item.latest_changed_at ? new Date(item.latest_changed_at).toLocaleString() : null,
        (item.risk_signals || []).join(", "),
      ].filter(Boolean).join(" | ");
      const facts = [
        ["risk", item.risk_level || "low", item.risk_level || "low"],
        ["agents", (item.active_agents || []).join(", ") || "none"],
        ["signals", formatNumber((item.risk_signals || []).length)],
      ];
      card.querySelector(".session-facts").replaceChildren(
        ...facts.map(([label, value, status]) => {
          const fact = document.createElement("span");
          fact.className = `badge ${badgeClass(status)}`;
          fact.textContent = `${label}: ${value}`;
          return fact;
        })
      );
      const rows = [
        ["File Claims", formatNumber(item.active_file_claims), item.active_file_claims],
        ["Symbol Claims", formatNumber(item.active_symbol_claims), item.active_symbol_claims],
        ["Recent Changes", formatNumber(item.changeset_count), item.changeset_count],
        [
          "Open Handoff Mentions",
          formatNumber(item.open_handoff_mentions),
          item.open_handoff_mentions,
        ],
        ["Code Graph Fan-out", formatNumber(item.graph_edges), item.graph_edges],
        ...((item.risk_signals || []).map((signal) => ["Risk Signal", signal, item.risk_level])),
      ];
      card.querySelector(".mini-list").replaceChildren(
        ...rows.map(([title, value, active]) => (
          row(title, value, active ? item.risk_level || "warning" : "ok", "evidence")
        ))
      );
      return card;
    }

    function changesetMeta(changeset) {
      return [
        changeset.git_commit ? shortId(changeset.git_commit) : null,
        changeset.branch,
        changeset.intent,
        changeset.created_at ? new Date(changeset.created_at).toLocaleString() : null,
      ].filter(Boolean).join(" | ");
    }

    function changesetCard(changeset) {
      const card = document.createElement("article");
      card.className = "session-card";
      card.innerHTML = `
        <div>
          <div class="title"></div>
          <div class="meta"></div>
        </div>
        <div class="session-facts"></div>
        <div class="mini-list"></div>`;
      card.querySelector(".title").textContent = changeset.summary || "Changeset";
      card.querySelector(".meta").textContent = changesetMeta(changeset);
      const facts = [
        ["files", formatNumber(changeset.file_count)],
        ["linked", formatNumber(changeset.linked_entity_count)],
        ["+", formatNumber(changeset.total_additions)],
        ["-", formatNumber(changeset.total_deletions)],
      ];
      card.querySelector(".session-facts").replaceChildren(
        ...facts.map(([label, value]) => {
          const item = document.createElement("span");
          item.className = "badge";
          item.textContent = `${label}: ${value}`;
          return item;
        })
      );
      const list = card.querySelector(".mini-list");
      if (!(changeset.files || []).length) {
        const empty = document.createElement("div");
        empty.className = "empty";
        empty.textContent = "No changed files recorded.";
        list.append(empty);
        return card;
      }
      for (const file of changeset.files.slice(0, 12)) {
        list.append(row(
          file.file_path,
          [`+${file.additions || 0}`, `-${file.deletions || 0}`].join(" | "),
          file.status || "ok",
          file.status || "file"
        ));
      }
      return card;
    }

    function renderChangesetBoard(payload) {
      const summary = payload?.summary || {};
      const changesets = payload?.changesets || [];
      renderStatCards(changesetSummary, [
        ["Changesets", formatNumber(summary.changesets)],
        ["Files", formatNumber(summary.files)],
        ["Linked", formatNumber(summary.linked_entities)],
        ["Additions", formatNumber(summary.additions)],
        ["Deletions", formatNumber(summary.deletions)],
      ]);
      changesetBoard.replaceChildren();
      if (!changesets.length) {
        const lane = document.createElement("section");
        lane.className = "session-lane";
        lane.innerHTML = `
          <div class="lane-head"><h2>No changesets</h2></div>
          <div class="lane-body"><div class="empty">No changesets recorded.</div></div>`;
        changesetBoard.append(lane);
        return;
      }
      const grouped = new Map();
      for (const changeset of changesets) {
        const key = changeset.branch || "unknown branch";
        if (!grouped.has(key)) grouped.set(key, []);
        grouped.get(key).push(changeset);
      }
      for (const [branch, branchChangesets] of grouped.entries()) {
        const lane = laneShell(branch, `${branchChangesets.length} changesets`);
        const body = lane.querySelector(".lane-body");
        for (const changeset of branchChangesets) body.append(changesetCard(changeset));
        changesetBoard.append(lane);
      }
    }

    function renderOrchestrationBoard(payload) {
      const summary = payload?.summary || {};
      const runs = payload?.runs || [];
      renderStatCards(orchestrationSummary, [
        ["Active Runs", formatNumber(summary.active_runs)],
        ["Ready", formatNumber(summary.ready_runs)],
        ["Blocked", formatNumber(summary.blocked_runs)],
        ["Approvals", formatNumber(summary.pending_approvals)],
        ["Workers", formatNumber(summary.active_workers)],
        ["Leases", formatNumber(summary.active_leases)],
        ["Ledger Pending", formatNumber(summary.degraded_pending)],
      ]);
      orchestrationBoard.replaceChildren();
      if (!runs.length) {
        const lane = document.createElement("section");
        lane.className = "session-lane";
        lane.innerHTML = `
          <div class="lane-head"><h2>No orchestrator runs</h2></div>
          <div class="lane-body"><div class="empty">No active runs recorded.</div></div>`;
        orchestrationBoard.append(lane);
        return;
      }
      const groups = new Map();
      for (const run of runs) {
        const key = run.readiness_status || "unknown";
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(run);
      }
      for (const [status, statusRuns] of groups.entries()) {
        const lane = laneShell(status, `${statusRuns.length} runs`);
        const body = lane.querySelector(".lane-body");
        for (const run of statusRuns) {
          const card = document.createElement("article");
          card.className = "session-card";
          card.innerHTML = `
            <div class="card-head">
              <h3></h3>
              <span class="badge ${badgeClass(run.readiness_status)}"></span>
            </div>
            <div class="meta"></div>
            <div class="chip-row"></div>
            <div class="mini-list"></div>`;
          card.querySelector("h3").textContent = run.title || run.run_id;
          card.querySelector(".badge").textContent = run.readiness_status || "unknown";
          card.querySelector(".meta").textContent = [
            run.risk_level,
            run.next_action,
            `evidence ${formatNumber(run.command_evidence_count)}`,
          ].filter(Boolean).join(" | ");
          card.querySelector(".chip-row").replaceChildren(
            ...[
              ["tasks", `${formatNumber(run.done_task_count)}/${formatNumber(run.task_count)}`],
              ["claimable", formatNumber(run.claimable_task_count)],
              ["workers", formatNumber(run.active_worker_count)],
              ["leases", formatNumber(run.active_lease_count)],
              ["findings", formatNumber(run.open_finding_count)],
              ["approvals", formatNumber(run.pending_approval_count)],
              ["ledger", formatNumber(run.degraded_ledger_pending_count)],
            ].map(([label, value]) => {
              const chip = document.createElement("span");
              chip.className = "badge";
              chip.textContent = `${label}: ${value}`;
              return chip;
            })
          );
          const list = card.querySelector(".mini-list");
          const reasons = run.blocking_reasons || [];
          if (!reasons.length) {
            const empty = document.createElement("div");
            empty.className = "empty";
            empty.textContent = "No active blockers.";
            list.append(empty);
          } else {
            for (const reason of reasons.slice(0, 6)) {
              list.append(row(String(reason), run.run_id, "warning", "blocker"));
            }
          }
          body.append(card);
        }
        orchestrationBoard.append(lane);
      }
    }

    function renderOrchestrationPlan(payload) {
      const summary = payload?.summary || {};
      const actions = payload?.recommended_actions || [];
      renderStatCards(orchestrationPlanSummary, [
        ["Plan Actions", formatNumber(actions.length)],
        ["Blocking", formatNumber(summary.blocking_action_count)],
        ["Dispatch", formatNumber(summary.dispatch_action_count)],
        ["Recovery", formatNumber(summary.recovery_command_count)],
      ]);
      orchestrationPlanBoard.replaceChildren();
      const lane = laneShell("Recommended Actions", `${actions.length} actions`);
      const body = lane.querySelector(".lane-body");
      if (!actions.length) {
        const empty = document.createElement("div");
        empty.className = "empty";
        empty.textContent = "No recommended actions.";
        body.append(empty);
      }
      for (const action of actions.slice(0, 20)) {
        const card = document.createElement("article");
        card.className = "session-card";
        card.innerHTML = `
          <div class="card-head">
            <h3></h3>
            <span class="badge"></span>
          </div>
          <div class="meta"></div>
          <div class="mini-list"></div>`;
        card.querySelector("h3").textContent = action.action_type || "action";
        card.querySelector(".badge").textContent = action.severity || "info";
        const severityClass = badgeClass(action.severity);
        if (severityClass) card.querySelector(".badge").classList.add(severityClass);
        card.querySelector(".meta").textContent = [
          action.reason,
          action.blocks_execution ? "blocks execution" : "runnable",
        ].filter(Boolean).join(" | ");
        const list = card.querySelector(".mini-list");
        if (action.suggested_cli_command) {
          list.append(row(action.suggested_cli_command, action.run_id || "", "ok", "command"));
        }
        const related = action.related_ids || {};
        for (const [key, value] of Object.entries(related)) {
          list.append(row(key, value, "active", "related"));
        }
        body.append(card);
      }
      orchestrationPlanBoard.append(lane);
    }

    function renderOrchestrationActions(payload) {
      const actions = payload?.actions || [];
      orchestrationActionBoard.replaceChildren();
      const lane = laneShell("Operator Actions", `${actions.length} actions`);
      const body = lane.querySelector(".lane-body");
      if (!actions.length) {
        const empty = document.createElement("div");
        empty.className = "empty";
        empty.textContent = "No operator actions available.";
        body.append(empty);
        orchestrationActionBoard.append(lane);
        return;
      }
      for (const action of actions.slice(0, 20)) {
        const card = document.createElement("article");
        card.className = "session-card";
        card.innerHTML = `
          <div class="card-head">
            <h3></h3>
            <span class="badge"></span>
          </div>
          <div class="meta"></div>
          <div class="mini-list"></div>`;
        card.querySelector("h3").textContent = action.label || action.action_type || "action";
        card.querySelector(".badge").textContent = action.status || action.severity || "ready";
        const actionClass = badgeClass(action.status || action.severity);
        if (actionClass) card.querySelector(".badge").classList.add(actionClass);
        card.querySelector(".meta").textContent = [
          action.reason,
          action.blocks_execution ? "blocks execution" : "runnable",
          action.run_id ? `run ${shortId(action.run_id)}` : null,
        ].filter(Boolean).join(" | ");
        const list = card.querySelector(".mini-list");
        if (action.suggested_cli_command) {
          list.append(row(action.suggested_cli_command, "suggested command", "ok", "command"));
        }
        for (const [key, value] of Object.entries(action.related_ids || {})) {
          list.append(row(key, value, "active", "related"));
        }
        for (const artifact of (action.artifact_refs || []).slice(0, 4)) {
          list.append(row("artifact", artifact, "ok", "artifact"));
        }
        body.append(card);
      }
      orchestrationActionBoard.append(lane);
    }

    function renderOrchestrationActionQueue(payload) {
      const items = payload?.items || [];
      orchestrationActionQueueBoard.replaceChildren();
      const lane = laneShell("Action Queue", `${items.length} queued records`);
      const body = lane.querySelector(".lane-body");
      if (!items.length) {
        const empty = document.createElement("div");
        empty.className = "empty";
        empty.textContent = "No queued operator actions.";
        body.append(empty);
        orchestrationActionQueueBoard.append(lane);
        return;
      }
      for (const item of items.slice(0, 20)) {
        const card = document.createElement("article");
        card.className = "session-card";
        card.innerHTML = `
          <div class="card-head">
            <h3></h3>
            <span class="badge"></span>
          </div>
          <div class="meta"></div>
          <div class="mini-list"></div>`;
        card.querySelector("h3").textContent = item.label || item.action_type || "queued action";
        card.querySelector(".badge").textContent = item.status || "queued";
        const itemClass = badgeClass(item.status);
        if (itemClass) card.querySelector(".badge").classList.add(itemClass);
        card.querySelector(".meta").textContent = [
          item.reason,
          item.approved_by ? `approved by ${item.approved_by}` : null,
          item.run_id ? `run ${shortId(item.run_id)}` : null,
        ].filter(Boolean).join(" | ");
        const list = card.querySelector(".mini-list");
        if (item.suggested_cli_command) {
          list.append(row(item.suggested_cli_command, "suggested command", "ok", "command"));
        }
        for (const [key, value] of Object.entries(item.related_ids || {})) {
          list.append(row(key, value, "active", "related"));
        }
        for (const artifact of (item.artifact_refs || []).slice(0, 4)) {
          list.append(row("artifact", artifact, "ok", "artifact"));
        }
        body.append(card);
      }
      orchestrationActionQueueBoard.append(lane);
    }

    function renderOrchestrationScheduler(payload) {
      orchestrationSchedulerBoard.replaceChildren();
      const selected = payload?.selected_actions || [];
      const skipped = payload?.skipped_actions || [];
      const budget = payload?.budget || {};
      const lane = laneShell(
        "Scheduler",
        `${selected.length} selected / ${skipped.length} skipped`
      );
      const body = lane.querySelector(".lane-body");
      const budgetCard = document.createElement("article");
      budgetCard.className = "session-card";
      budgetCard.innerHTML = `
        <div class="card-head">
          <h3>Budget</h3>
          <span class="badge"></span>
        </div>
        <div class="meta"></div>
        <div class="mini-list"></div>`;
      budgetCard.querySelector(".badge").textContent = payload?.execution_status || "preview";
      budgetCard.querySelector(".meta").textContent = [
        `actions ${budget.selected_actions ?? 0}/${budget.budget_actions ?? "open"}`,
        `spawn ${budget.selected_spawn_actions ?? 0}/${budget.budget_spawn_actions ?? "open"}`,
      ].join(" | ");
      body.append(budgetCard);
      for (const item of selected.slice(0, 12)) {
        const card = document.createElement("article");
        card.className = "session-card";
        card.innerHTML = `
          <div class="card-head">
            <h3></h3>
            <span class="badge ok">selected</span>
          </div>
          <div class="meta"></div>
          <div class="mini-list"></div>`;
        card.querySelector("h3").textContent = item.label || item.action_type || "action";
        card.querySelector(".meta").textContent = [
          item.action_type,
          item.approved_by ? `approved by ${item.approved_by}` : null,
          item.run_id ? `run ${shortId(item.run_id)}` : null,
        ].filter(Boolean).join(" | ");
        const list = card.querySelector(".mini-list");
        if (item.suggested_cli_command) {
          list.append(row(item.suggested_cli_command, "suggested command", "ok", "command"));
        }
        for (const artifact of (item.artifact_refs || []).slice(0, 3)) {
          list.append(row("artifact", artifact, "ok", "artifact"));
        }
        body.append(card);
      }
      for (const item of skipped.slice(0, 8)) {
        const card = document.createElement("article");
        card.className = "session-card";
        card.innerHTML = `
          <div class="card-head">
            <h3></h3>
            <span class="badge warning">skipped</span>
          </div>
          <div class="meta"></div>`;
        card.querySelector("h3").textContent = item.label || item.action_type || "action";
        card.querySelector(".meta").textContent = item.skip_reason || "not runnable";
        body.append(card);
      }
      orchestrationSchedulerBoard.append(lane);
    }

    function renderOrchestrationTraces(payload) {
      const runs = payload?.runs || [];
      orchestrationTraceBoard.replaceChildren();
      const lane = laneShell("Control Traces", `${runs.length} runs`);
      const body = lane.querySelector(".lane-body");
      if (!runs.length) {
        const empty = document.createElement("div");
        empty.className = "empty";
        empty.textContent = "No control or planner trace artifacts.";
        body.append(empty);
        orchestrationTraceBoard.append(lane);
        return;
      }
      for (const run of runs.slice(0, 20)) {
        const trace = run.latest_control_trace || {};
        const control = run.latest_control_bundle || {};
        const proposal = run.latest_task_graph_proposal || {};
        const planner = run.latest_planner_invocation || {};
        const card = document.createElement("article");
        card.className = "session-card";
        card.innerHTML = `
          <div class="card-head">
            <h3></h3>
            <span class="badge"></span>
          </div>
          <div class="meta"></div>
          <div class="chip-row"></div>
          <div class="mini-list"></div>`;
        card.querySelector("h3").textContent = run.title || run.run_id;
        const status = (
          trace.step_status || control.execution_status || planner.status || "no-trace"
        );
        card.querySelector(".badge").textContent = status;
        const statusClass = badgeClass(status);
        if (statusClass) card.querySelector(".badge").classList.add(statusClass);
        card.querySelector(".meta").textContent = [
          trace.action_type || control.next_action,
          proposal.proposal_id ? `proposal ${shortId(proposal.proposal_id)}` : null,
          planner.planner_agent ? `planner ${planner.planner_agent}` : null,
          run.updated_at ? new Date(run.updated_at).toLocaleString() : null,
        ].filter(Boolean).join(" | ");
        card.querySelector(".chip-row").replaceChildren(
          ...[
            ["control", control.control_id || "none"],
            ["action", trace.action_type || control.next_action || "none"],
            ["planner", planner.planner_agent || proposal.planner || "template"],
            ["tasks", formatNumber(proposal.task_count || planner.task_count)],
          ].map(([label, value]) => {
            const chip = document.createElement("span");
            chip.className = "badge";
            chip.textContent = `${label}: ${value}`;
            return chip;
          })
        );
        const list = card.querySelector(".mini-list");
        if (trace.delegated_command || control.delegated_command) {
          list.append(row(
            trace.delegated_command || control.delegated_command,
            "delegated command",
            "ok",
            "command"
          ));
        }
        for (const task of [
          ...(proposal.selected_tasks || []),
          ...(planner.selected_tasks || []),
        ].slice(0, 6)) {
          list.append(row(
            task.key || task.title,
            task.depends_on?.length ? `depends on ${task.depends_on.join(", ")}` : "task",
            "active",
            "task"
          ));
        }
        const paths = {
          ...(control.artifact_paths || {}),
          ...(trace.artifact_paths || {}),
          ...(planner.artifact_paths || {}),
        };
        for (const [label, value] of Object.entries(paths).slice(0, 6)) {
          list.append(row(label, value, "ok", "artifact"));
        }
        if (!list.children.length) {
          const empty = document.createElement("div");
          empty.className = "empty";
          empty.textContent = "No redacted trace details for this run.";
          list.append(empty);
        }
        body.append(card);
      }
      orchestrationTraceBoard.append(lane);
    }

    function graphCounts(items, key) {
      return items.reduce((counts, item) => {
        const value = item[key] || "unknown";
        counts[value] = (counts[value] || 0) + 1;
        return counts;
      }, {});
    }

    function renderGraphDrilldown(payload) {
      const nodes = payload?.nodes || [];
      const edges = payload?.edges || [];
      const nodeKinds = graphCounts(nodes, "kind");
      const edgeKinds = graphCounts(edges, "kind");
      renderStatCards(graphSummary, [
        ["Nodes", formatNumber(nodes.length)],
        ["Edges", formatNumber(edges.length)],
        ["Agents", formatNumber(nodeKinds.agent)],
        ["Sessions", formatNumber(nodeKinds.session)],
        ["Changesets", formatNumber(nodeKinds.changeset)],
        ["Handoffs", formatNumber(nodeKinds.handoff_summary)],
      ]);
      renderList(
        graphNodes,
        nodes.slice(0, 80),
        "No lineage nodes for this workspace.",
        (node) => row(
          node.title || node.raw_id || node.id,
          [node.kind, node.source, node.status, node.occurred_at].filter(Boolean).join(" | "),
          node.status || "ok",
          node.kind
        )
      );
      renderList(
        graphEdges,
        edges.slice(0, 120),
        "No lineage edges for this workspace.",
        (edge) => row(
          edge.kind || "edge",
          `${edge.source || "unknown"} -> ${edge.target || "unknown"}`,
          edge.kind === "precedes" ? "warning" : "ok",
          formatNumber(edgeKinds[edge.kind || "unknown"])
        )
      );
    }

    function relationshipPanel(title, rows, emptyText) {
      const panel = document.createElement("section");
      panel.innerHTML = `
        <header><h2></h2></header>
        <div class="panel"><div class="list"></div></div>`;
      panel.querySelector("h2").textContent = title;
      const list = panel.querySelector(".list");
      if (!rows.length) {
        const empty = document.createElement("div");
        empty.className = "empty";
        empty.textContent = emptyText;
        list.append(empty);
      } else {
        for (const item of rows) list.append(item);
      }
      return panel;
    }

    function agentSessionRows(sessions) {
      const grouped = new Map();
      for (const session of sessions) {
        const key = agentKey(session.agent_name);
        if (!grouped.has(key)) grouped.set(key, []);
        grouped.get(key).push(session);
      }
      return [...grouped.entries()].map(([agent, agentSessions]) => {
        const totals = agentSessions.reduce((acc, session) => {
          const counts = roleCounts(session);
          acc.messages += session.message_count || 0;
          acc.readable += sessionReadableCount(session);
          acc.user += counts.user || 0;
          acc.agent += counts.agent || 0;
          acc.captured += counts.captured || 0;
          acc.technical += counts.technical || 0;
          return acc;
        }, { messages: 0, readable: 0, user: 0, agent: 0, captured: 0, technical: 0 });
        return row(
          agent,
          [
            `${agentSessions.length} sessions`,
            `${totals.user} user prompts`,
            `${totals.agent} agent replies`,
            `${totals.captured} captured`,
            `${totals.readable} readable`,
            `${totals.technical} technical`,
          ].join(" | "),
          totals.readable ? "ok" : "warning",
          "evidence"
        );
      });
    }

    function agentWorkRows(overview) {
      const claims = [
        ...(overview.active_reservations?.files || []),
        ...(overview.active_reservations?.symbols || []),
      ].map((item) => row(
        agentKey(item.agent_name),
        item.file_path || item.qualified_name || item.symbol || item.purpose,
        item.status || "ok",
        item.file_path ? "file" : "symbol"
      ));
      const handoffs = (overview.open_handoffs || []).map((handoff) => row(
        `${handoff.from_agent_name || "agent"} -> ${handoff.to_agent_name || "unassigned"}`,
        handoff.next_action || handoff.summary,
        handoff.status || "open",
        "handoff"
      ));
      return [...claims, ...handoffs];
    }

    function evidenceTimelineRows(events, sessions) {
      const sessionRows = sessions.slice(0, 4).map((session) => {
        const signal = sessionConversationState(session);
        return row(
          session.title || session.external_id || shortId(session.session_id),
          [agentKey(session.agent_name), sessionEvidenceMeta(session)].join(" | "),
          signal.status,
          "session"
        );
      });
      const eventRows = events.slice(0, 8).map((event) => row(
        event.title || event.kind,
        [event.agent_name, event.kind, event.artifact_type].filter(Boolean).join(" | "),
        event.status || "ok",
        event.kind
      ));
      return [...sessionRows, ...eventRows];
    }

    function renderTraceModel(overview, events, sessions) {
      const traceGrid = document.querySelector("#trace-grid");
      traceGrid.replaceChildren(
        relationshipPanel(
          "Agent To Sessions",
          agentSessionRows(sessions),
          "No session evidence for this workspace."
        ),
        relationshipPanel(
          "Agent To Work",
          agentWorkRows(overview),
          "No active reservations or open handoffs."
        ),
        relationshipPanel(
          "Evidence To Timeline",
          evidenceTimelineRows(events, sessions),
          "No sessions or timeline events."
        )
      );
    }

    async function loadDashboard(options = {}) {
      if (state.loading) return;
      const workspace = workspaceInput.value.trim();
      const limit = limitInput.value;
      if (!workspace) {
        setStatus("No workspace selected.", true);
        return;
      }
      state.loading = true;
      state.workspace = workspace;
      state.limit = limit;
      state.activityFilters = readActivityFilters();
      updateWorkspaceMeta();
      if (!options.silent) setStatus("Loading dashboard data...");
      const encoded = encodeURIComponent(workspace);
      const overviewUrl = `/api/workspaces/${encoded}/overview?limit=${encodeURIComponent(limit)}`;
      const activityParams = new URLSearchParams({ limit });
      setOptionalQueryParam(activityParams, "kind", state.activityFilters.kind);
      setOptionalQueryParam(activityParams, "agent", state.activityFilters.agent);
      setOptionalQueryParam(activityParams, "status", state.activityFilters.status);
      const activityUrl = `/api/workspaces/${encoded}/activity?${activityParams.toString()}`;
      const lineageUrl = `/api/workspaces/${encoded}/lineage?limit=${encodeURIComponent(limit)}`;
      const projectUrl = `/api/workspaces/${encoded}/project?limit=${encodeURIComponent(limit)}`;
      const codeRiskUrl = `/api/workspaces/${encoded}/code-risk?limit=${encodeURIComponent(limit)}`;
      const changesetsUrl = [
        `/api/workspaces/${encoded}/changesets?limit=${encodeURIComponent(limit)}`
      ].join("");
      const usageUrl = `/api/workspaces/${encoded}/usage`;
      const orchestrationUrl = [
        `/api/workspaces/${encoded}/orchestration?limit=${encodeURIComponent(limit)}`
      ].join("");
      const orchestrationPlanUrl = [
        `/api/workspaces/${encoded}/orchestration-plan?limit=${encodeURIComponent(limit)}`
      ].join("");
      const orchestrationActionUrl = [
        `/api/workspaces/${encoded}/orchestration-actions?limit=${encodeURIComponent(limit)}`
      ].join("");
      const orchestrationActionQueueUrl = [
        `/api/workspaces/${encoded}/orchestration-action-queue?limit=${encodeURIComponent(limit)}`
      ].join("");
      const orchestrationSchedulerUrl = [
        `/api/workspaces/${encoded}/orchestration-scheduler?limit=${encodeURIComponent(limit)}`
      ].join("");
      const orchestrationTraceUrl = [
        `/api/workspaces/${encoded}/orchestration-traces?limit=${encodeURIComponent(limit)}`
      ].join("");
      const handoffsUrl = `/api/workspaces/${encoded}/handoffs?limit=${encodeURIComponent(limit)}`;
      const sessionsUrl = [
        `/api/workspaces/${encoded}/sessions?limit=${encodeURIComponent(limit)}`,
        "message_limit=30"
      ].join("&");
      let overviewResponse;
      let activityResponse;
      let lineageResponse;
      let sessionsResponse;
      let projectResponse;
      let codeRiskResponse;
      let changesetsResponse;
      let usageResponse;
      let orchestrationResponse;
      let orchestrationPlanResponse;
      let orchestrationActionResponse;
      let orchestrationActionQueueResponse;
      let orchestrationSchedulerResponse;
      let orchestrationTraceResponse;
      let handoffResponse;
      try {
        [
          overviewResponse,
          activityResponse,
          lineageResponse,
          sessionsResponse,
          projectResponse,
          codeRiskResponse,
          changesetsResponse,
          usageResponse,
          orchestrationResponse,
          orchestrationPlanResponse,
          orchestrationActionResponse,
          orchestrationActionQueueResponse,
          orchestrationSchedulerResponse,
          orchestrationTraceResponse,
          handoffResponse,
        ] = await Promise.all([
          fetch(overviewUrl),
          fetch(activityUrl),
          fetch(lineageUrl),
          fetch(sessionsUrl),
          fetch(projectUrl),
          fetch(codeRiskUrl),
          fetch(changesetsUrl),
          fetch(usageUrl),
          fetch(orchestrationUrl),
          fetch(orchestrationPlanUrl),
          fetch(orchestrationActionUrl),
          fetch(orchestrationActionQueueUrl),
          fetch(orchestrationSchedulerUrl),
          fetch(orchestrationTraceUrl),
          fetch(handoffsUrl),
        ]);
        const overview = await overviewResponse.json();
        const activity = await activityResponse.json();
        const lineagePayload = await lineageResponse.json();
        const sessionPayload = await sessionsResponse.json();
        const projectPayload = await projectResponse.json();
        const codeRiskPayload = await codeRiskResponse.json();
        const changesetsPayload = await changesetsResponse.json();
        const usagePayload = await usageResponse.json();
        const orchestrationPayload = await orchestrationResponse.json();
        const orchestrationPlanPayload = await orchestrationPlanResponse.json();
        const orchestrationActionPayload = await orchestrationActionResponse.json();
        const orchestrationActionQueuePayload = await orchestrationActionQueueResponse.json();
        const orchestrationSchedulerPayload = await orchestrationSchedulerResponse.json();
        const orchestrationTracePayload = await orchestrationTraceResponse.json();
        const handoffPayload = await handoffResponse.json();
        if (!overviewResponse.ok || overview.status === "not_found") {
          setStatus(`Workspace not found: ${workspace}`, true);
          return;
        }
      const files = overview.active_reservations?.files || [];
      const symbols = overview.active_reservations?.symbols || [];
      renderList(
        reservations,
        [...files, ...symbols],
        "No active reservations.",
        (item) => row(
          item.file_path || item.qualified_name || item.symbol,
          item.purpose || item.agent_name,
          "active"
        )
      );
      renderList(
        handoffs,
        overview.open_handoffs || [],
        "No open handoffs.",
        (item) => row(
          item.summary,
          `${item.from_agent_name || ""} -> ${item.to_agent_name || "unassigned"}`,
          item.status
        )
      );
      lineageNodes.textContent = overview.lineage?.node_count ?? 0;
      lineageEdges.textContent = overview.lineage?.edge_count ?? 0;
      const events = activity.events || overview.recent_activity || [];
      const sessions = sessionPayload.sessions || [];
      state.overview = overview;
      state.events = events;
      state.sessions = sessions;
      state.lineage = lineagePayload;
      state.project = projectPayload;
      state.codeRisk = codeRiskPayload;
      state.changesets = changesetsPayload.changesets || [];
      state.usage = usagePayload;
      state.orchestration = orchestrationPayload;
      state.orchestrationPlan = orchestrationPlanPayload;
      state.orchestrationActions = orchestrationActionPayload;
      state.orchestrationActionQueue = orchestrationActionQueuePayload;
      state.orchestrationScheduler = orchestrationSchedulerPayload;
      state.orchestrationTraces = orchestrationTracePayload;
      state.handoffs = handoffPayload.handoffs || [];
      renderMetrics(overview.counts || {}, collectAgentKeys(events, sessions, overview).length);
      renderProjectTree(projectPayload);
      renderGraphDrilldown(lineagePayload);
      renderCodeRiskBoard(codeRiskPayload);
      renderChangesetBoard(changesetsPayload);
      renderUsageBoard(usagePayload);
      renderOrchestrationBoard(orchestrationPayload);
      renderOrchestrationPlan(orchestrationPlanPayload);
      renderOrchestrationActions(orchestrationActionPayload);
      renderOrchestrationActionQueue(orchestrationActionQueuePayload);
      renderOrchestrationScheduler(orchestrationSchedulerPayload);
      renderOrchestrationTraces(orchestrationTracePayload);
      renderHandoffBoard(handoffPayload);
      renderAgentBoard(events, sessions, overview);
      renderSessionBoard(sessions);
      renderTimeline(events);
      renderTraceModel(overview, events, sessions);
        updateUrl();
        const loadedAt = new Date().toLocaleTimeString();
        setStatus(`Loaded ${overview.workspace_name || workspace} · ${loadedAt}`);
      } catch (error) {
        setStatus(`Dashboard refresh failed: ${error}`, true);
      } finally {
        state.loading = false;
      }
    }

    async function startDashboard() {
      await loadRuntimeInfo();
      await loadWorkspaceOptions();
      if (workspaceInput.value.trim()) await loadDashboard();
      state.autoRefreshSeconds = Number(refreshInput.value || 0);
      scheduleAutoRefresh();
    }

    startDashboard();
  </script>
</body>
</html>"""


def match_workspace_route(path: str) -> tuple[str, str] | None:
    parts = [unquote(part) for part in path.strip("/").split("/") if part]
    if len(parts) != 4 or parts[0] != "api" or parts[1] != "workspaces":
        return None
    endpoint = parts[3]
    if endpoint not in {
        "overview",
        "activity",
        "timeline",
        "lineage",
        "reservations",
        "handoffs",
        "sessions",
        "project",
        "code-risk",
        "changesets",
        "usage",
        "orchestration",
        "orchestration-plan",
        "orchestration-actions",
        "orchestration-action-queue",
        "orchestration-scheduler",
        "orchestration-traces",
    }:
        return None
    return parts[2], endpoint


def query_limit(query: str, default: int = 100) -> int:
    return query_int(query, "limit", default=default, maximum=500)


def query_int(query: str, key: str, *, default: int, maximum: int) -> int:
    values = parse_qs(query).get(key)
    if not values:
        return default
    try:
        return max(1, min(int(values[0]), maximum))
    except ValueError:
        return default


def status_for_payload(payload: dict[str, Any]) -> int:
    return 404 if payload.get("status") == "not_found" else 200


def open_browser(url: str) -> None:
    import webbrowser

    webbrowser.open(url)
