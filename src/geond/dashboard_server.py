from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from geond.config import Settings
from geond.db import connect
from geond.storage.dashboard import (
    get_agent_activity_events,
    get_dashboard_overview,
    get_dashboard_sessions,
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
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            return

    return DashboardHandler


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
        if endpoint == "sessions":
            message_limit = query_int(parsed.query, "message_limit", default=4, maximum=20)
            payload = get_dashboard_sessions(
                conn,
                workspace_id,
                limit=limit,
                message_limit=message_limit,
            )
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
            "/api/workspaces/{workspace_id}/sessions",
        ],
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
      --bg: #f6f7f9;
      --surface: #ffffff;
      --surface-2: #eef2f6;
      --text: #18202a;
      --muted: #5d6978;
      --line: #d7dde5;
      --accent: #0b6b63;
      --accent-soft: #dcefeb;
      --warn: #9a5b00;
      --danger: #a33a34;
      --ok: #167044;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system,
        BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-size: 14px;
      letter-spacing: 0;
      height: 100vh;
      overflow: hidden;
    }
    header {
      height: 64px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 18px;
      padding: 0 24px;
      border-bottom: 1px solid var(--line);
      background: var(--surface);
      position: sticky;
      top: 0;
      z-index: 5;
    }
    h1 {
      margin: 0;
      font-size: 18px;
      font-weight: 680;
    }
    main {
      height: calc(100vh - 64px);
      padding: 12px 14px 14px;
      overflow: hidden;
    }
    .toolbar {
      display: grid;
      grid-template-columns: minmax(220px, 1fr) auto auto;
      gap: 10px;
      align-items: center;
      width: min(780px, 100%);
    }
    label {
      display: grid;
      gap: 4px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 600;
    }
    input, select, button {
      height: 36px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--text);
      font: inherit;
    }
    input { padding: 0 10px; min-width: 0; }
    select { padding: 0 28px 0 10px; }
    button {
      padding: 0 12px;
      background: var(--accent);
      color: #fff;
      border-color: var(--accent);
      font-weight: 650;
      cursor: pointer;
    }
    button.secondary {
      background: #fff;
      color: var(--text);
      border-color: var(--line);
    }
    .status-line {
      min-height: 20px;
      margin: 0 0 10px;
      color: var(--muted);
      font-size: 13px;
    }
    .tabs {
      display: flex;
      gap: 8px;
      margin-bottom: 10px;
      overflow-x: auto;
    }
    .tab {
      height: 32px;
      background: var(--surface);
      color: var(--text);
      border-color: var(--line);
      white-space: nowrap;
    }
    .tab.active {
      background: var(--accent);
      color: #fff;
      border-color: var(--accent);
    }
    .view {
      display: none;
      height: calc(100% - 62px);
      min-height: 0;
    }
    .view.active {
      display: block;
    }
    .overview-shell {
      display: grid;
      grid-template-columns: 360px minmax(0, 1fr);
      gap: 12px;
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
      grid-auto-columns: minmax(330px, 380px);
      gap: 12px;
      height: 100%;
      min-width: 0;
      overflow-x: auto;
      overflow-y: hidden;
      padding-bottom: 8px;
    }
    .agent-lane, .session-lane {
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      min-height: 0;
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }
    .lane-head {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 8px;
      align-items: start;
      padding: 12px;
      border-bottom: 1px solid var(--line);
      background: #fff;
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
    }
    details.collapsible > summary {
      cursor: pointer;
      padding: 9px 10px;
      font-weight: 680;
      list-style-position: inside;
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
    }
    .session-card {
      display: grid;
      gap: 8px;
    }
    .message {
      background: var(--surface-2);
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
    .trace-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(260px, 1fr));
      gap: 12px;
      height: 100%;
      overflow: auto;
      align-items: stretch;
    }
    section {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }
    section > header {
      position: static;
      height: 48px;
      padding: 0 14px;
      border-bottom: 1px solid var(--line);
      background: var(--surface);
    }
    section h2 {
      margin: 0;
      font-size: 14px;
      font-weight: 720;
    }
    .metrics {
      display: grid;
      grid-template-columns: repeat(4, minmax(96px, 1fr));
      border-bottom: 1px solid var(--line);
    }
    .metric {
      padding: 14px;
      border-right: 1px solid var(--line);
      min-height: 78px;
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
    .badge.ok { color: var(--ok); background: #e5f3ec; border-color: #b9dec8; }
    .badge.warn { color: var(--warn); background: #fff3db; border-color: #eed39c; }
    .badge.danger { color: var(--danger); background: #fae8e6; border-color: #e5b8b3; }
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
    }
    .event:last-child { border-bottom: 0; }
    .event time {
      color: var(--muted);
      font-size: 12px;
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
    @media (max-width: 980px) {
      header { height: auto; padding: 14px; align-items: stretch; flex-direction: column; }
      .toolbar { grid-template-columns: 1fr; width: 100%; }
      main { height: calc(100vh - 145px); padding: 10px; }
      .overview-shell, .split, .lineage, .trace-grid { grid-template-columns: 1fr; }
      .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .panel + .panel, .lineage div + div { border-left: 0; border-top: 1px solid var(--line); }
      .event { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1>Geond Agent Activity</h1>
    <form class="toolbar" id="controls">
      <label>
        Workspace id or URI
        <input id="workspace" name="workspace"
          placeholder="workspace id or file:///..." autocomplete="off" />
      </label>
      <label>
        Limit
        <select id="limit" name="limit">
          <option>25</option>
          <option selected>50</option>
          <option>100</option>
          <option>250</option>
        </select>
      </label>
      <button type="submit">Refresh</button>
    </form>
  </header>
  <main>
    <div class="status-line" id="status">Enter a workspace id or URI to load dashboard data.</div>
    <nav class="tabs" aria-label="Dashboard views">
      <button class="tab active" type="button" data-view="mission">Mission Control</button>
      <button class="tab" type="button" data-view="sessions">Sessions</button>
      <button class="tab" type="button" data-view="timeline">Timeline</button>
      <button class="tab" type="button" data-view="trace">Trace Model</button>
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
        <div class="agent-board" id="agent-board"></div>
      </div>
    </section>
    <section class="view" data-view-panel="sessions">
      <div class="session-board" id="session-board"></div>
    </section>
    <section class="view" data-view-panel="timeline">
      <section>
        <header>
          <h2>Activity Timeline</h2>
          <span class="badge" id="activity-count">0 events</span>
        </header>
        <div class="timeline" id="timeline"></div>
      </section>
    </section>
    <section class="view" data-view-panel="trace">
      <div class="trace-grid">
        <section>
          <header><h2>Graph UI Pattern</h2></header>
          <div class="panel">
            <div class="row">
              <div>
                <div class="title">Fast graph, localhost first</div>
                <div class="meta">
                  Use the lineage payload before adding a heavier graph canvas.
                </div>
              </div>
              <span class="badge ok">read-only</span>
            </div>
          </div>
        </section>
        <section>
          <header><h2>Trace And Hooks</h2></header>
          <div class="panel">
            <div class="row">
              <div>
                <div class="title">Session, tool, validation, stop</div>
                <div class="meta">Normalize Codex/Claude hook events into the activity stream.</div>
              </div>
              <span class="badge warn">next</span>
            </div>
          </div>
        </section>
        <section>
          <header><h2>Handoff Lifecycle</h2></header>
          <div class="panel">
            <div class="row">
              <div>
                <div class="title">Open, blocked, closed, released</div>
                <div class="meta">
                  Tie PM/orchestration state to reservations and code evidence.
                </div>
              </div>
              <span class="badge ok">linked</span>
            </div>
          </div>
        </section>
      </div>
    </section>
  </main>
  <script>
    const state = { workspace: "", limit: 50 };
    const qs = new URLSearchParams(location.search);
    const workspaceInput = document.querySelector("#workspace");
    const limitInput = document.querySelector("#limit");
    const statusLine = document.querySelector("#status");
    const metrics = document.querySelector("#metrics");
    const reservations = document.querySelector("#reservations");
    const handoffs = document.querySelector("#handoffs");
    const agentBoard = document.querySelector("#agent-board");
    const sessionBoard = document.querySelector("#session-board");
    const timeline = document.querySelector("#timeline");
    const countBadge = document.querySelector("#activity-count");
    const lineageNodes = document.querySelector("#lineage-nodes");
    const lineageEdges = document.querySelector("#lineage-edges");
    const copyApi = document.querySelector("#copy-api");

    workspaceInput.value = qs.get("workspace") || "";
    limitInput.value = qs.get("limit") || "50";

    document.querySelector("#controls").addEventListener("submit", (event) => {
      event.preventDefault();
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

    function setStatus(message, error = false) {
      statusLine.textContent = message;
      statusLine.style.color = error ? "var(--danger)" : "var(--muted)";
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
      if (["expired", "warning"].includes(status)) return "warn";
      if (["released", "closed", "error", "failed"].includes(status)) return "danger";
      return "";
    }

    function row(title, meta, status) {
      const item = document.createElement("div");
      item.className = "row";
      item.innerHTML = `
        <div><div class="title"></div><div class="meta"></div></div>
        <span class="badge ${badgeClass(status)}"></span>`;
      item.querySelector(".title").textContent = title || "Untitled";
      item.querySelector(".meta").textContent = meta || "";
      item.querySelector(".badge").textContent = status || "unknown";
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

    function renderMetrics(counts) {
      const entries = [
        ["Agents", counts.agents],
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

    function renderTimeline(events) {
      timeline.replaceChildren();
      countBadge.textContent = `${events.length} events`;
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
        timeline.append(item);
      }
    }

    function agentKey(name) {
      return name || "system";
    }

    function eventAgentName(event) {
      return agentKey(event.agent_name);
    }

    function sessionCard(session) {
      const card = document.createElement("article");
      card.className = "session-card";
      card.innerHTML = `
        <div>
          <div class="title"></div>
          <div class="meta"></div>
        </div>
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
      const messages = card.querySelector(".mini-list");
      if (!(session.messages || []).length) {
        const empty = document.createElement("div");
        empty.className = "empty";
        empty.textContent = "No stored messages.";
        messages.append(empty);
        return card;
      }
      for (const message of session.messages || []) {
        const item = document.createElement("div");
        item.className = "message";
        item.innerHTML = `<div class="role"></div><div class="content"></div>`;
        item.querySelector(".role").textContent = `${message.role} #${message.ordinal}`;
        item.querySelector(".content").textContent = message.content || "";
        messages.append(item);
      }
      return card;
    }

    function laneShell(name, summary) {
      const lane = document.createElement("section");
      lane.className = "agent-lane";
      lane.innerHTML = `
        <div class="lane-head">
          <div>
            <h2></h2>
            <div class="meta"></div>
          </div>
          <span class="badge"></span>
        </div>
        <div class="lane-body"></div>`;
      lane.querySelector("h2").textContent = name;
      lane.querySelector(".meta").textContent = summary || "";
      lane.querySelector(".badge").textContent = "agent";
      return lane;
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
      for (const event of events) ensure(eventAgentName(event)).events.push(event);
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
      if (!ordered.length) {
        const lane = laneShell("No agents", "No activity recorded yet.");
        agentBoard.append(lane);
        return;
      }
      for (const [name, laneData] of ordered) {
        const lane = laneShell(
          name,
          `${laneData.sessions.length} sessions | ${laneData.events.length} events`
        );
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
          "Sessions",
          laneData.sessions,
          "No sessions for this agent.",
          sessionCard,
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
              <h2></h2>
              <div class="meta"></div>
            </div>
            <span class="badge"></span>
          </div>
          <div class="lane-body"></div>`;
        lane.querySelector("h2").textContent = session.agent_name || session.source;
        lane.querySelector(".meta").textContent = session.external_id || "";
        lane.querySelector(".badge").textContent = session.source;
        lane.querySelector(".lane-body").append(sessionCard(session));
        sessionBoard.append(lane);
      }
    }

    async function loadDashboard() {
      const workspace = workspaceInput.value.trim();
      const limit = limitInput.value;
      if (!workspace) {
        setStatus("Enter a workspace id or URI to load dashboard data.", true);
        return;
      }
      state.workspace = workspace;
      state.limit = limit;
      setStatus("Loading dashboard data...");
      const encoded = encodeURIComponent(workspace);
      const overviewUrl = `/api/workspaces/${encoded}/overview?limit=${encodeURIComponent(limit)}`;
      const activityUrl = `/api/workspaces/${encoded}/activity?limit=${encodeURIComponent(limit)}`;
      const sessionsUrl = [
        `/api/workspaces/${encoded}/sessions?limit=${encodeURIComponent(limit)}`,
        "message_limit=5"
      ].join("&");
      const [overviewResponse, activityResponse, sessionsResponse] = await Promise.all([
        fetch(overviewUrl),
        fetch(activityUrl),
        fetch(sessionsUrl),
      ]);
      const overview = await overviewResponse.json();
      const activity = await activityResponse.json();
      const sessionPayload = await sessionsResponse.json();
      if (!overviewResponse.ok || overview.status === "not_found") {
        setStatus(`Workspace not found: ${workspace}`, true);
        return;
      }
      renderMetrics(overview.counts || {});
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
      renderAgentBoard(events, sessions, overview);
      renderSessionBoard(sessions);
      renderTimeline(events);
      const url = new URL(location.href);
      url.searchParams.set("workspace", workspace);
      url.searchParams.set("limit", limit);
      history.replaceState(null, "", url);
      setStatus(`Loaded ${overview.workspace_name || workspace}`);
    }

    if (workspaceInput.value.trim()) loadDashboard();
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
