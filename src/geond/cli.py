from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from geond.adapters.claude_code import parse_storage as parse_claude_code_storage
from geond.adapters.claude_code import to_summary as claude_code_to_summary
from geond.adapters.codex import parse_storage as parse_codex_storage
from geond.adapters.codex import to_summary as codex_to_summary
from geond.adapters.manus import AGENT_NAME as MANUS_AGENT_NAME
from geond.adapters.manus import ManusApiClient, ManusApiError, excerpt_message, load_fixture
from geond.adapters.vscode_copilot import parse_storage, to_summary
from geond.cli_tasks import (
    finish_task,
    format_task_result_markdown,
    parse_changed_files,
    start_task,
)
from geond.code_graph.lsp_collector import (
    collect_lsp_references,
    list_lsp_server_profiles,
    resolve_lsp_server_command,
)
from geond.code_graph.lsp_references import normalize_lsp_references
from geond.code_graph.python_indexer import index_python_path
from geond.code_graph.tree_sitter_indexer import index_tree_sitter_path
from geond.code_graph.ts_js_indexer import index_ts_js_path
from geond.config import get_settings
from geond.dashboard_server import serve_dashboard
from geond.db import connect, discover_schema_files, run_schema_file, run_schema_migrations
from geond.doctor import collect_doctor_report, format_doctor_report
from geond.embeddings import get_embedding_provider
from geond.install import (
    SUPPORTED_INSTALL_CLIENTS,
    format_install_result_text,
    install_clients,
)
from geond.mcp_smoke import format_smoke_report, run_stdio_smoke
from geond.retrieval.simple import (
    explain_change,
    get_changeset_detail,
    get_symbol_context,
    hybrid_search_dev_memory,
    search_dev_memory,
    vector_search_dev_memory,
)
from geond.storage.benchmark import (
    benchmark_search,
    compare_benchmark_runs,
    format_benchmark_report_markdown,
    list_benchmark_runs,
    load_judgments,
    save_benchmark_run,
)
from geond.storage.code_graph import store_code_index, store_lsp_references
from geond.storage.context_review import format_context_review_markdown, review_workspace_context
from geond.storage.dashboard import (
    get_agent_activity_events,
    get_dashboard_changesets,
    get_dashboard_code_risk,
    get_dashboard_manus_sessions,
    get_dashboard_overview,
    get_dashboard_usage,
)
from geond.storage.embeddings import embed_pending_messages, embedding_stats
from geond.storage.maintenance import purge_workspace, seed_sample_workspace
from geond.storage.repository import (
    cleanup_expired_reservations_for_workspace,
    close_handoff_summary,
    get_workspace_coordination_policy,
    list_active_file_reservations,
    list_active_symbol_reservations,
    list_handoff_summaries,
    list_reservation_events,
    list_workspace_aliases,
    record_agent_action,
    record_changeset,
    record_handoff_summary,
    record_workspace_fingerprints,
    register_workspace_alias,
    release_reservation,
    release_symbol_reservation,
    renew_reservation,
    renew_symbol_reservation,
    reserve_files,
    reserve_symbols,
    resolve_workspace_id,
    set_workspace_coordination_policy,
    store_claude_code_session,
    store_codex_session,
    store_manus_task,
    store_vscode_session,
    suggest_workspace_aliases,
    upsert_workspace,
)
from geond.storage.resources import get_workspace_lineage
from geond.storage.usage import (
    build_usage_risk_signals,
    format_usage_group_markdown,
    format_usage_risk_signals_markdown,
    format_usage_summary_markdown,
    record_claude_code_usage_events,
    record_codex_usage_events,
    record_vscode_copilot_usage_events,
    summarize_usage,
    usage_group_report,
)
from geond.workspace_identity import (
    discover_workspace_fingerprints,
    workspace_uri_from_path_or_uri,
)


def workspace_uri_from_cwd(cwd: object) -> str:
    if not isinstance(cwd, str) or not cwd.strip():
        return "claude-code://unknown-workspace"
    normalized = cwd.strip().replace("\\", "/")
    if re.match(r"^[A-Za-z]:/", normalized):
        return f"file:///{normalized}"
    if normalized.startswith("/"):
        return f"file://{normalized}"
    return f"file:///{normalized}"


def workspace_name_from_uri(workspace_uri: str) -> str:
    normalized = workspace_uri.rstrip("/").replace("\\", "/")
    if normalized.startswith("file://"):
        normalized = normalized.removeprefix("file://")
    name = normalized.rsplit("/", 1)[-1]
    return name or "claude-code-workspace"


def changed_files_from_args(
    file_paths: list[str],
    status: str,
    patch_files: list[Path] | None,
) -> list[dict[str, object]]:
    patches: list[str | None] = [None] * len(file_paths)
    if patch_files:
        if len(patch_files) == 1 and len(file_paths) == 1:
            patches[0] = patch_files[0].read_text(encoding="utf-8")
        elif len(patch_files) == len(file_paths):
            patches = [path.read_text(encoding="utf-8") for path in patch_files]
        else:
            raise SystemExit("--patch-file must be provided once or once per --file")
    return [
        {"file_path": file_path, "status": status, "patch": patches[index]}
        for index, file_path in enumerate(file_paths)
    ]


def format_benchmark_result_markdown(result: dict[str, object]) -> str:
    lines = [
        "# Benchmark Result",
        "",
        f"- Mode: `{result.get('mode')}`",
        f"- Repeat: `{result.get('repeat')}`",
        f"- Limit: `{result.get('limit')}`",
        "",
        "| Query | Results | Min ms | Avg ms | Max ms | Recall@k | MRR | nDCG@k |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for query in result.get("queries", []):
        if not isinstance(query, dict):
            continue
        quality = query.get("quality") if isinstance(query.get("quality"), dict) else {}
        row_template = (
            "| {query} | {result_count} | {min_ms} | {avg_ms} | {max_ms} | "
            "{recall} | {mrr} | {ndcg} |"
        )
        lines.append(
            row_template.format(
                query=str(query.get("query") or "").replace("|", "\\|"),
                result_count=query.get("result_count") or 0,
                min_ms=query.get("min_ms") or "",
                avg_ms=query.get("avg_ms") or "",
                max_ms=query.get("max_ms") or "",
                recall=quality.get("recall_at_k") or "",
                mrr=quality.get("mrr") or "",
                ndcg=quality.get("ndcg_at_k") or "",
            )
        )
    return "\n".join(lines)


def require_workspace_id(conn, workspace_id_or_uri: str) -> str:
    workspace_id = resolve_workspace_id(conn, workspace_id_or_uri)
    if not workspace_id:
        raise SystemExit(f"Workspace not found: {workspace_id_or_uri}")
    return workspace_id


def configure_cli_output() -> None:
    """Prefer UTF-8 output on Windows consoles that default to legacy code pages."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        return


def fingerprint_from_arg(value: str) -> dict[str, object]:
    if "=" not in value:
        raise SystemExit("--fingerprint must use TYPE=VALUE")
    fingerprint_type, fingerprint_value = value.split("=", 1)
    return {
        "fingerprint_type": fingerprint_type.strip(),
        "fingerprint_value": fingerprint_value.strip(),
        "metadata": {"source": "cli"},
    }


def _mask_task_url(task_dict: dict, show_private: bool) -> dict:
    """Replace task_url/share_url with '[private]' unless public or show_private."""
    if show_private or task_dict.get("share_visibility") == "public":
        return task_dict
    masked = dict(task_dict)
    if "task_url" in masked:
        masked["task_url"] = "[private]"
    if "share_url" in masked:
        masked["share_url"] = "[private]"
    return masked


def build_manus_context_packet(
    conn: object,
    workspace_uri: str,
    query: str = "",
    limit: int = 5,
) -> dict:
    """Assemble a Geond context packet suitable for pasting into a Manus task prompt."""
    from psycopg import Connection as _Conn

    assert isinstance(conn, _Conn)

    ctx = review_workspace_context(
        conn,
        workspace_id_or_uri=workspace_uri,
        intent=query,
        limit=limit,
    )
    workspace_id = ctx.get("workspace_id")

    recent_activity: list[dict] = []
    if workspace_id:
        activity = get_agent_activity_events(conn, workspace_uri, limit=limit * 2)
        recent_activity = activity.get("events", [])[:limit]

    search_results: list[dict] = []
    if query and workspace_id:
        hits = search_dev_memory(conn, query=query, workspace_uri=workspace_uri, limit=limit)
        for hit in hits:
            search_results.append(
                {
                    "source": hit.get("source"),
                    "session_title": hit.get("title"),
                    "role": hit.get("role"),
                    "ordinal": hit.get("ordinal"),
                    "excerpt": excerpt_message(hit.get("content") or ""),
                    "evidence_ref": (
                        f"geond:{hit.get('source')}:{hit.get('external_id')}:{hit.get('ordinal')}"
                    ),
                }
            )

    open_handoffs = [
        {
            "handoff_id": h.get("handoff_id"),
            "from_agent": h.get("from_agent_name"),
            "to_agent": h.get("to_agent_name"),
            "summary": h.get("summary"),
            "next_steps": h.get("next_steps"),
            "blocked_on": h.get("blocked_on"),
        }
        for h in ctx.get("loaded_context", {}).get("open_handoffs", [])
    ]

    file_reservations = [
        {
            "file_path": r.get("file_path"),
            "agent": r.get("agent_name"),
            "purpose": r.get("purpose"),
            "expires_at": r.get("expires_at"),
        }
        for r in ctx.get("loaded_context", {}).get("file_reservations", [])
    ]

    symbol_reservations = [
        {
            "symbol": r.get("symbol"),
            "agent": r.get("agent_name"),
            "purpose": r.get("purpose"),
        }
        for r in ctx.get("loaded_context", {}).get("symbol_reservations", [])
    ]

    blocked_manus_tasks: list[dict] = []
    if workspace_id:
        manus_result = get_dashboard_manus_sessions(conn, workspace_uri, limit=limit * 2)
        for t in manus_result.get("tasks") or []:
            if t.get("is_blocked"):
                blocked_manus_tasks.append(
                    {
                        "task_id": t.get("task_id"),
                        "title": t.get("title"),
                        "status": t.get("status"),
                        "task_url": t.get("task_url"),
                        "excerpt": t.get("excerpt"),
                    }
                )

    return {
        "schema": "geond.context_packet.v1",
        "workspace_uri": workspace_uri,
        "workspace_id": workspace_id,
        "query": query,
        "open_handoffs": open_handoffs,
        "active_file_reservations": file_reservations,
        "active_symbol_reservations": symbol_reservations,
        "recent_activity": recent_activity,
        "search_results": search_results,
        "blocked_manus_tasks": blocked_manus_tasks,
        "assessment": ctx.get("assessment"),
        "recommendations": ctx.get("recommendations", []),
    }


def _context_packet_to_prompt(packet: dict) -> str:
    """Render a context packet as a Manus task prompt string."""
    lines: list[str] = [
        "## Geond Context Packet",
        f"workspace: {packet.get('workspace_uri')}",
        f"query: {packet.get('query') or '(none)'}",
        "",
    ]

    handoffs = packet.get("open_handoffs") or []
    if handoffs:
        lines.append(f"### Open Handoffs ({len(handoffs)})")
        for h in handoffs:
            lines.append(f"- [{h.get('from_agent')} → {h.get('to_agent')}] {h.get('summary')}")
            for step in h.get("next_steps") or []:
                lines.append(f"  - {step}")
        lines.append("")

    reservations = packet.get("active_file_reservations") or []
    if reservations:
        lines.append(f"### Active File Reservations ({len(reservations)})")
        for r in reservations:
            lines.append(
                f"- {r.get('file_path')} (agent: {r.get('agent')}, purpose: {r.get('purpose')})"
            )
        lines.append("")

    sym_reservations = packet.get("active_symbol_reservations") or []
    if sym_reservations:
        lines.append(f"### Active Symbol Reservations ({len(sym_reservations)})")
        for r in sym_reservations:
            lines.append(f"- {r.get('symbol')} (agent: {r.get('agent')})")
        lines.append("")

    results = packet.get("search_results") or []
    if results:
        lines.append(f"### Relevant Prior Sessions ({len(results)})")
        for r in results:
            lines.append(
                f"- [{r.get('evidence_ref')}] {r.get('session_title')} / "
                f"{r.get('role')} #{r.get('ordinal')}: {r.get('excerpt')}"
            )
        lines.append("")

    recs = packet.get("recommendations") or []
    if recs:
        lines.append("### Geond Recommendations")
        for rec in recs:
            lines.append(f"- {rec}")
        lines.append("")

    blocked = packet.get("blocked_manus_tasks") or []
    if blocked:
        lines.append(f"### Blocked Manus Tasks ({len(blocked)})")
        for t in blocked:
            url_part = f" [{t.get('task_url')}]" if t.get("task_url") else ""
            lines.append(
                f"- [{t.get('task_id')}]{url_part} {t.get('title')} (status: {t.get('status')})"
            )
            if t.get("excerpt"):
                lines.append(f"  last: {t.get('excerpt')[:200]}")
        lines.append("")

    return "\n".join(lines)


def _build_task_contract(
    start_result: dict,
    intent: str,
    expected_outputs: list[str],
    validation_commands: list[str],
) -> dict:
    """Build a structured pre-task contract from start_task output."""
    reservations = start_result.get("reservations") or {}
    file_res = reservations.get("files") or {}
    sym_res = reservations.get("symbols") or {}

    file_reservation_ids = [
        r.get("reservation_id")
        for r in (file_res.get("reservations") or [])
        if r.get("reservation_id")
    ]
    symbol_reservation_ids = [
        r.get("reservation_id")
        for r in (sym_res.get("reservations") or [])
        if r.get("reservation_id")
    ]

    conflicts = start_result.get("conflicts") or {}
    return {
        "schema": "geond.manus_task_contract.v1",
        "workspace_uri": start_result.get("review", {}).get("workspace_uri"),
        "workspace_id": start_result.get("workspace_id"),
        "agent_name": start_result.get("agent_name"),
        "intent": intent,
        "files": (start_result.get("requested") or {}).get("files") or [],
        "symbols": (start_result.get("requested") or {}).get("symbols") or [],
        "active_conflicts": {
            "file_reservations": conflicts.get("file_reservations") or [],
            "symbol_reservations": conflicts.get("symbol_reservations") or [],
        },
        "reservation_ids": {
            "files": file_reservation_ids,
            "symbols": symbol_reservation_ids,
        },
        "expected_outputs": expected_outputs,
        "validation_commands": validation_commands,
        "action_id": start_result.get("action_id"),
        "dry_run": start_result.get("dry_run", False),
        "recommendations": (start_result.get("review") or {}).get("recommendations") or [],
    }


def _contract_to_prompt(contract: dict) -> str:
    """Render a task contract as a Manus task prompt string."""
    lines: list[str] = [
        "## Geond Task Contract",
        f"workspace: {contract.get('workspace_uri') or contract.get('workspace_id')}",
        f"intent: {contract.get('intent')}",
        "",
    ]

    files = contract.get("files") or []
    if files:
        lines.append("### Files in Scope")
        for f in files:
            lines.append(f"- {f}")
        lines.append("")

    symbols = contract.get("symbols") or []
    if symbols:
        lines.append("### Symbols in Scope")
        for s in symbols:
            lines.append(f"- {s}")
        lines.append("")

    conflicts = contract.get("active_conflicts") or {}
    file_conflicts = conflicts.get("file_reservations") or []
    sym_conflicts = conflicts.get("symbol_reservations") or []
    if file_conflicts or sym_conflicts:
        lines.append("### Active Conflicts (check before editing)")
        for r in file_conflicts:
            lines.append(
                f"- FILE {r.get('file_path')} reserved by {r.get('agent_name')}: {r.get('purpose')}"
            )
        for r in sym_conflicts:
            lines.append(
                f"- SYMBOL {r.get('symbol')} reserved by {r.get('agent_name')}: {r.get('purpose')}"
            )
        lines.append("")

    res_ids = contract.get("reservation_ids") or {}
    file_ids = res_ids.get("files") or []
    sym_ids = res_ids.get("symbols") or []
    if file_ids or sym_ids:
        lines.append("### Geond Reservation IDs (include in task output)")
        for rid in file_ids:
            lines.append(f"- file:{rid}")
        for rid in sym_ids:
            lines.append(f"- symbol:{rid}")
        lines.append("")

    expected = contract.get("expected_outputs") or []
    if expected:
        lines.append("### Expected Outputs")
        for e in expected:
            lines.append(f"- {e}")
        lines.append("")

    cmds = contract.get("validation_commands") or []
    if cmds:
        lines.append("### Validation Commands")
        for cmd in cmds:
            lines.append(f"- {cmd}")
        lines.append("")

    recs = contract.get("recommendations") or []
    if recs:
        lines.append("### Geond Recommendations")
        for rec in recs:
            lines.append(f"- {rec}")
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    configure_cli_output()
    parser = argparse.ArgumentParser(prog="geond")
    subparsers = parser.add_subparsers(dest="command", required=True)

    migrate = subparsers.add_parser("migrate", help="Apply SQL schema files")
    migrate.add_argument(
        "--schema",
        dest="schemas",
        type=Path,
        action="append",
        help="Schema file to apply immediately; repeatable. Defaults to schemas/001_initial.sql.",
    )
    migrate.add_argument(
        "--all",
        action="store_true",
        help="Apply sorted schema migrations once using schema_migrations.",
    )
    migrate.add_argument("--schemas-dir", type=Path, default=Path("schemas"))

    doctor = subparsers.add_parser("doctor", help="Check local Geond setup")
    doctor.add_argument("--format", choices=["json", "text"], default="json")
    doctor.add_argument("--skip-db", action="store_true", help="Skip live Postgres checks")
    doctor.add_argument("--skip-mcp", action="store_true", help="Skip MCP registration checks")
    doctor.add_argument("--strict", action="store_true", help="Exit non-zero when errors are found")

    mcp_smoke = subparsers.add_parser(
        "mcp-smoke",
        help="Start geond-mcp as a stdio server and exercise it with a real MCP client",
    )
    mcp_smoke.add_argument("--format", choices=["json", "text"], default="json")
    mcp_smoke.add_argument("--query", default="app_context")
    mcp_smoke.add_argument("--workspace-uri", default="file:///sample/geond")
    mcp_smoke.add_argument("--limit", type=int, default=3)
    mcp_smoke.add_argument(
        "--server-command",
        default="uv",
        help="Command used to start the MCP server",
    )
    mcp_smoke.add_argument(
        "--server-arg",
        action="append",
        help=(
            "Argument for the MCP server command; repeat to override the default "
            "`--directory <repo> run geond-mcp` args"
        ),
    )
    mcp_smoke.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when the smoke status is warning or error",
    )
    mcp_smoke.add_argument(
        "--allow-empty-search",
        action="store_true",
        help="Treat an empty search result as ok; useful for structural MCP transport checks",
    )

    install = subparsers.add_parser(
        "install",
        help="Preview or write MCP/editor client configuration for Geond",
    )
    install.add_argument(
        "--client",
        dest="clients",
        choices=["all", *SUPPORTED_INSTALL_CLIENTS],
        action="append",
        help="Client config to install; repeatable. Defaults to vscode-mcp and vscode-lsp-task.",
    )
    install.add_argument("--repo-root", type=Path, default=Path.cwd())
    install.add_argument("--workspace-root", type=Path, default=Path.cwd())
    install.add_argument("--config-path", type=Path)
    install.add_argument("--server-name", default="geond")
    install.add_argument(
        "--database-url",
        default="postgresql://geond:geond_dev_password@localhost:55432/geond",
    )
    install.add_argument("--database-profile", default="local")
    install.add_argument("--privacy-mode", default="local-only")
    install.add_argument("--embedding-provider", default="none")
    install.add_argument("--embedding-model")
    install.add_argument("--write", action="store_true", help="Write changes to config files")
    install.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow replacing existing Continue YAML config instead of preview-only output",
    )
    install.add_argument("--format", choices=["json", "text"], default="json")

    dashboard_overview = subparsers.add_parser(
        "dashboard-overview",
        help="Return a read-only dashboard overview for one workspace",
    )
    dashboard_overview.add_argument("workspace_id_or_uri")
    dashboard_overview.add_argument("--limit", type=int, default=25)

    dashboard_events = subparsers.add_parser(
        "dashboard-events",
        help="Return normalized agent activity events for one workspace",
    )
    dashboard_events.add_argument("workspace_id_or_uri")
    dashboard_events.add_argument("--limit", type=int, default=100)
    dashboard_events.add_argument("--kind", dest="event_kind")
    dashboard_events.add_argument("--agent", dest="agent_name")
    dashboard_events.add_argument("--status")

    dashboard_code_risk = subparsers.add_parser(
        "dashboard-code-risk",
        help="Return dashboard code-risk hot files for one workspace",
    )
    dashboard_code_risk.add_argument("workspace_id_or_uri")
    dashboard_code_risk.add_argument("--limit", type=int, default=100)

    dashboard_changesets = subparsers.add_parser(
        "dashboard-changesets",
        help="Return dashboard changeset review feed for one workspace",
    )
    dashboard_changesets.add_argument("workspace_id_or_uri")
    dashboard_changesets.add_argument("--limit", type=int, default=50)

    dashboard_graph = subparsers.add_parser(
        "dashboard-graph",
        help="Return bounded dashboard lineage graph nodes and edges for one workspace",
    )
    dashboard_graph.add_argument("workspace_id_or_uri")
    dashboard_graph.add_argument("--limit", type=int, default=100)

    dashboard = subparsers.add_parser("dashboard", help="Run local dashboard commands")
    dashboard_subparsers = dashboard.add_subparsers(dest="dashboard_command", required=True)
    dashboard_serve = dashboard_subparsers.add_parser(
        "serve",
        help="Serve the read-only local dashboard API",
    )
    dashboard_serve.add_argument("--host", default="127.0.0.1")
    dashboard_serve.add_argument("--port", type=int, default=8765)
    dashboard_serve.add_argument("--open", action="store_true", help="Open the dashboard URL")

    usage_summary = subparsers.add_parser(
        "usage-summary",
        help="Summarize normalized LLM usage events for one workspace",
    )
    usage_summary.add_argument("workspace_id_or_uri")
    usage_summary.add_argument("--source")
    usage_summary.add_argument("--provider")
    usage_summary.add_argument("--model")
    usage_summary.add_argument("--format", choices=["json", "markdown"], default="json")

    usage_by_agent = subparsers.add_parser(
        "usage-by-agent",
        help="Group normalized LLM usage events by agent for one workspace",
    )
    usage_by_agent.add_argument("workspace_id_or_uri")
    usage_by_agent.add_argument("--source")
    usage_by_agent.add_argument("--provider")
    usage_by_agent.add_argument("--model")
    usage_by_agent.add_argument("--format", choices=["json", "markdown"], default="json")

    usage_by_model = subparsers.add_parser(
        "usage-by-model",
        help="Group normalized LLM usage events by provider and model for one workspace",
    )
    usage_by_model.add_argument("workspace_id_or_uri")
    usage_by_model.add_argument("--source")
    usage_by_model.add_argument("--provider")
    usage_by_model.add_argument("--model")
    usage_by_model.add_argument("--format", choices=["json", "markdown"], default="json")

    usage_risk = subparsers.add_parser(
        "usage-risk-signals",
        help="Return review signals that compare usage with evidence for one workspace",
    )
    usage_risk.add_argument("workspace_id_or_uri")
    usage_risk.add_argument("--format", choices=["json", "markdown"], default="json")

    parse_vscode = subparsers.add_parser(
        "parse-vscode", help="Parse VS Code Copilot Chat storage without writing to DB"
    )
    parse_vscode.add_argument("storage_path", type=Path)
    parse_vscode.add_argument("--session-id")

    parse_codex = subparsers.add_parser(
        "parse-codex", help="Parse Codex JSONL sessions without writing to DB"
    )
    parse_codex.add_argument("storage_path", type=Path)
    parse_codex.add_argument("--session-id")
    parse_codex.add_argument("--limit", type=int)

    parse_claude = subparsers.add_parser(
        "parse-claude-code", help="Parse Claude Code JSONL sessions without writing to DB"
    )
    parse_claude.add_argument("storage_path", nargs="?", type=Path)
    parse_claude.add_argument("--session-id")
    parse_claude.add_argument("--limit", type=int)

    import_vscode = subparsers.add_parser(
        "import-vscode", help="Import VS Code Copilot Chat storage into Geond DB"
    )
    import_vscode.add_argument("storage_path", type=Path)
    import_vscode.add_argument("--session-id")
    import_vscode.add_argument("--workspace-uri", required=True)
    import_vscode.add_argument("--workspace-name", required=True)

    import_codex = subparsers.add_parser("import-codex", help="Import Codex JSONL sessions")
    import_codex.add_argument("storage_path", type=Path)
    import_codex.add_argument("--session-id")
    import_codex.add_argument("--limit", type=int)
    import_codex.add_argument("--workspace-uri", required=True)
    import_codex.add_argument("--workspace-name", required=True)

    import_claude = subparsers.add_parser(
        "import-claude-code", help="Import Claude Code JSONL sessions"
    )
    import_claude.add_argument("storage_path", nargs="?", type=Path)
    import_claude.add_argument("--session-id")
    import_claude.add_argument("--limit", type=int)
    import_claude.add_argument("--workspace-uri")
    import_claude.add_argument("--workspace-name")

    import_manus = subparsers.add_parser(
        "import-manus-task", help="Import a Manus API v2 task into Geond"
    )
    import_manus.add_argument("task_id_arg", nargs="?", help="Manus task ID")
    import_manus.add_argument("--task-id", help="Manus task ID (requires MANUS_API_KEY)")
    import_manus.add_argument(
        "--fixture",
        metavar="DETAIL_JSON",
        help="Path to task_detail JSON fixture (skips API call)",
    )
    import_manus.add_argument(
        "--fixture-messages",
        metavar="MESSAGES_JSON",
        help="Path to task_messages JSON fixture (optional, used with --fixture)",
    )
    import_manus.add_argument(
        "--workspace-uri",
        default="",
        help="Workspace URI (required unless --dry-run)",
    )
    import_manus.add_argument("--workspace-name")
    import_manus.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned records without writing to the database",
    )

    manus_ctx = subparsers.add_parser(
        "manus-context-packet",
        help="Build a Geond context packet to send as a Manus task prompt",
    )
    manus_ctx.add_argument("--workspace-uri", required=True)
    manus_ctx.add_argument("--query", default="", help="Intent / task description for search")
    manus_ctx.add_argument("--limit", type=int, default=5, help="Items per section")
    manus_ctx.add_argument(
        "--create-task",
        action="store_true",
        help="Create a Manus task with this packet as the prompt (requires MANUS_API_KEY)",
    )
    manus_ctx.add_argument(
        "--task-title",
        default="",
        help="Title for the Manus task (used with --create-task)",
    )

    manus_contract = subparsers.add_parser(
        "manus-task-contract",
        help="Create a pre-task contract for Manus with workspace context and reservations",
    )
    manus_contract.add_argument("--workspace-uri", required=True)
    manus_contract.add_argument("--intent", required=True, help="What Manus should do")
    manus_contract.add_argument("--file", dest="files", action="append", metavar="FILE")
    manus_contract.add_argument("--symbol", dest="symbols", action="append", metavar="SYMBOL")
    manus_contract.add_argument(
        "--expected-output",
        dest="expected_outputs",
        action="append",
        metavar="OUTPUT",
    )
    manus_contract.add_argument(
        "--validation-command",
        dest="validation_commands",
        action="append",
        metavar="CMD",
    )
    manus_contract.add_argument(
        "--reserve",
        action="store_true",
        help="Claim file/symbol reservations for Manus",
    )
    manus_contract.add_argument("--ttl-minutes", type=int, default=120)
    manus_contract.add_argument("--override-reason")
    manus_contract.add_argument("--limit", type=int, default=5)
    manus_contract.add_argument("--dry-run", action="store_true")
    manus_contract.add_argument(
        "--format", choices=["json", "prompt"], default="json", help="Output format"
    )

    manus_complete = subparsers.add_parser(
        "manus-task-complete",
        help="Import a completed Manus task and record handoff + release reservations",
    )
    manus_complete.add_argument("task_id_arg", nargs="?", help="Manus task ID")
    manus_complete.add_argument(
        "--task-id", help="Manus task ID to import (requires MANUS_API_KEY)"
    )
    manus_complete.add_argument("--fixture", metavar="DETAIL_JSON")
    manus_complete.add_argument("--fixture-messages", metavar="MESSAGES_JSON")
    manus_complete.add_argument("--workspace-uri", required=True)
    manus_complete.add_argument("--workspace-name")
    manus_complete.add_argument(
        "--handoff-summary",
        default="",
        help="Summary of what Manus accomplished",
    )
    manus_complete.add_argument(
        "--next-step",
        dest="next_steps",
        action="append",
        metavar="STEP",
    )
    manus_complete.add_argument(
        "--tested-command",
        dest="tested_commands",
        action="append",
        metavar="CMD",
    )
    manus_complete.add_argument(
        "--remaining-risk",
        dest="remaining_risks",
        action="append",
        metavar="RISK",
    )
    manus_complete.add_argument("--next-action", default="")
    manus_complete.add_argument("--to-agent", default="", help="Handoff target agent name")
    manus_complete.add_argument(
        "--reservation-mode",
        choices=["release", "keep", "renew"],
        default="release",
    )
    manus_complete.add_argument("--dry-run", action="store_true")

    manus_list = subparsers.add_parser(
        "list-manus-tasks",
        help="List Manus tasks from the API (requires MANUS_API_KEY)",
    )
    manus_list.add_argument("--limit", type=int, default=20, help="Max tasks to fetch")
    manus_list.add_argument(
        "--status",
        help="Filter by status (e.g. completed, failed, running)",
    )
    manus_list.add_argument(
        "--format",
        choices=["json", "table"],
        default="table",
        help="Output format",
    )
    manus_list.add_argument(
        "--show-private-url",
        action="store_true",
        default=False,
        help="Include task_url even when share_visibility is not public",
    )

    manus_bulk = subparsers.add_parser(
        "import-manus-tasks",
        help="Bulk import Manus tasks from the API into Geond (requires MANUS_API_KEY)",
    )
    manus_bulk.add_argument("--workspace-uri", required=True)
    manus_bulk.add_argument("--workspace-name")
    manus_bulk.add_argument("--limit", type=int, default=20, help="Max tasks to import")
    manus_bulk.add_argument("--status", help="Filter by status (e.g. completed)")
    manus_bulk.add_argument(
        "--include-files",
        action="store_true",
        help="Fetch and store file artifact metadata for each task",
    )
    manus_bulk.add_argument("--dry-run", action="store_true")

    manus_get_file = subparsers.add_parser(
        "manus-get-file",
        help="Download a file artifact from a Manus task (requires MANUS_API_KEY)",
    )
    manus_get_file.add_argument("--task-id", required=True, help="Manus task ID")
    manus_get_file.add_argument("--file-id", required=True, help="Manus file ID")
    manus_get_file.add_argument(
        "--output",
        help="Write file content to this path (default: stdout as binary)",
    )
    manus_get_file.add_argument(
        "--max-bytes",
        type=int,
        default=10 * 1024 * 1024,
        help="Max bytes to download (default: 10 MB)",
    )

    manus_dashboard = subparsers.add_parser(
        "manus-dashboard",
        help="Show Manus task cards stored in Geond for a workspace",
    )
    manus_dashboard.add_argument("--workspace-uri", required=True)
    manus_dashboard.add_argument("--limit", type=int, default=30)
    manus_dashboard.add_argument("--format", choices=["json", "table"], default="table")

    embed_messages = subparsers.add_parser(
        "embed-messages", help="Create embeddings for imported message records"
    )
    embed_messages.add_argument("--limit", type=int, default=100)
    embed_messages.add_argument("--batch-size", type=int, default=32)

    search = subparsers.add_parser("search", help="Search imported development memory")
    search.add_argument("query")
    search.add_argument("--mode", choices=["keyword", "vector", "hybrid"], default="hybrid")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--workspace-uri")
    search.add_argument("--source")
    search.add_argument("--rerank", choices=["none", "local", "api"], default="none")
    search.add_argument("--candidate-limit", type=int)

    index_python = subparsers.add_parser(
        "index-python", help="Index Python files into the code graph tables"
    )
    index_python.add_argument("path", type=Path)
    index_python.add_argument("--workspace-uri", required=True)
    index_python.add_argument("--workspace-name", required=True)
    index_python.add_argument("--root", type=Path)

    index_ts_js = subparsers.add_parser(
        "index-ts-js", help="Index TypeScript/JavaScript files into the code graph tables"
    )
    index_ts_js.add_argument("path", type=Path)
    index_ts_js.add_argument("--workspace-uri", required=True)
    index_ts_js.add_argument("--workspace-name", required=True)
    index_ts_js.add_argument("--root", type=Path)

    index_tree_sitter = subparsers.add_parser(
        "index-tree-sitter",
        help="Index Python/TypeScript/JavaScript files with tree-sitter precision",
    )
    index_tree_sitter.add_argument("path", type=Path)
    index_tree_sitter.add_argument("--workspace-uri", required=True)
    index_tree_sitter.add_argument("--workspace-name", required=True)
    index_tree_sitter.add_argument("--root", type=Path)

    import_lsp = subparsers.add_parser(
        "import-lsp-references",
        help="Import LSP reference edges from a JSON file",
    )
    import_lsp.add_argument("workspace_id")
    import_lsp.add_argument("path", type=Path)
    import_lsp.add_argument("--append", action="store_true")
    import_lsp.add_argument("--workspace-root")
    import_lsp.add_argument("--target-qualified-name")
    import_lsp.add_argument("--provider")

    normalize_lsp = subparsers.add_parser(
        "normalize-lsp-references",
        help="Normalize VS Code/LSP reference locations to Geond reference JSON",
    )
    normalize_lsp.add_argument("path", type=Path)
    normalize_lsp.add_argument("--workspace-root")
    normalize_lsp.add_argument("--target-qualified-name")
    normalize_lsp.add_argument("--provider")
    normalize_lsp.add_argument("--output", type=Path)

    collect_lsp = subparsers.add_parser(
        "collect-lsp-references",
        help="Call a stdio language server and write VS Code/LSP Location reference payloads",
    )
    collect_lsp.add_argument("path", type=Path, help="File containing the target symbol")
    collect_lsp.add_argument("--line", type=int, required=True, help="1-based target line")
    collect_lsp.add_argument("--character", type=int, default=0, help="0-based target character")
    collect_lsp.add_argument("--server-command", help="Quoted stdio LSP command")
    collect_lsp.add_argument(
        "--server-profile",
        default="auto",
        choices=["auto", "pyright", "typescript"],
        help="Built-in stdio LSP command profile used when --server-command is omitted",
    )
    collect_lsp.add_argument("--workspace-root", type=Path)
    collect_lsp.add_argument("--language-id")
    collect_lsp.add_argument("--target-qualified-name")
    collect_lsp.add_argument("--provider")
    collect_lsp.add_argument("--timeout-seconds", type=float, default=10.0)
    collect_lsp.add_argument(
        "--no-include-declaration",
        dest="include_declaration",
        action="store_false",
        default=True,
    )
    collect_lsp.add_argument("--output", type=Path, help="Write collected Location payload JSON")
    collect_lsp.add_argument(
        "--import-workspace-id",
        help="Import normalized references into this workspace id or URI after collection",
    )
    collect_lsp.add_argument(
        "--append",
        action="store_true",
        help="Append instead of replacing LSP edges",
    )

    subparsers.add_parser(
        "lsp-server-profiles",
        help="List built-in stdio LSP server profiles for collect-lsp-references",
    )

    seed_sample = subparsers.add_parser(
        "seed-sample", help="Insert a small sample workspace and session"
    )
    seed_sample.add_argument("--schema", type=Path, default=Path("schemas/001_initial.sql"))

    purge = subparsers.add_parser("purge-workspace", help="Delete one workspace and its data")
    purge.add_argument("workspace_id_or_uri")
    purge.add_argument("--yes", action="store_true", help="Confirm deletion")

    register_alias = subparsers.add_parser(
        "register-workspace-alias",
        help="Register a moved or renamed workspace URI as an alias",
    )
    register_alias.add_argument("workspace_id_or_uri")
    register_alias.add_argument("alias_uri")
    register_alias.add_argument("--reason", default="moved")

    aliases = subparsers.add_parser("workspace-aliases", help="List workspace aliases")
    aliases.add_argument("--workspace-id-or-uri")

    workspace_policy = subparsers.add_parser(
        "workspace-policy",
        help="Get or set workspace coordination policy",
    )
    workspace_policy.add_argument("workspace_id_or_uri")
    workspace_policy.add_argument(
        "--reservation-conflict-policy",
        choices=["advisory", "strict", "override-with-reason"],
    )

    fingerprint_workspace = subparsers.add_parser(
        "fingerprint-workspace",
        help="Record git-derived fingerprints for an existing workspace",
    )
    fingerprint_workspace.add_argument("workspace_id_or_uri")
    fingerprint_workspace.add_argument("path_or_uri")
    fingerprint_workspace.add_argument(
        "--fingerprint",
        action="append",
        help="Extra fingerprint as TYPE=VALUE; can be repeated",
    )

    suggest_aliases = subparsers.add_parser(
        "suggest-workspace-aliases",
        help="Suggest existing workspaces for a moved folder using fingerprints",
    )
    suggest_aliases.add_argument("path_or_uri")
    suggest_aliases.add_argument(
        "--fingerprint",
        action="append",
        help="Fingerprint as TYPE=VALUE; can be repeated",
    )
    suggest_aliases.add_argument(
        "--register-best",
        action="store_true",
        help="Register the alias when exactly one high-confidence suggestion is found",
    )
    suggest_aliases.add_argument("--min-confidence", type=float, default=0.75)

    conflicts = subparsers.add_parser("conflicts", help="List active file and symbol reservations")
    conflicts.add_argument("workspace_id")
    conflicts.add_argument("--file", dest="files", action="append")
    conflicts.add_argument("--symbol", dest="symbols", action="append")

    review_context = subparsers.add_parser(
        "review-context",
        help="Compare requested work with reservations, handoffs, and lineage",
    )
    review_context.add_argument("workspace_id_or_uri")
    review_context.add_argument("--intent", default="")
    review_context.add_argument("--file", dest="files", action="append")
    review_context.add_argument("--symbol", dest="symbols", action="append")
    review_context.add_argument("--agent-name")
    review_context.add_argument("--limit", type=int, default=5)
    review_context.add_argument("--format", choices=["json", "markdown"], default="json")

    start_task_cmd = subparsers.add_parser(
        "start-task",
        help="Read coordination context, record task_start, and optionally reserve work",
    )
    start_task_cmd.add_argument("workspace_id_or_uri")
    start_task_cmd.add_argument("--agent-name", required=True)
    start_task_cmd.add_argument("--intent", required=True)
    start_task_cmd.add_argument("--file", dest="files", action="append")
    start_task_cmd.add_argument("--symbol", dest="symbols", action="append")
    start_task_cmd.add_argument("--reserve", action="store_true")
    start_task_cmd.add_argument("--ttl-minutes", type=int, default=120)
    start_task_cmd.add_argument("--override-reason")
    start_task_cmd.add_argument("--dry-run", action="store_true")
    start_task_cmd.add_argument("--limit", type=int, default=5)
    start_task_cmd.add_argument("--session-id")
    start_task_cmd.add_argument("--session-external-id")
    start_task_cmd.add_argument("--format", choices=["json", "markdown"], default="json")

    finish_task_cmd = subparsers.add_parser(
        "finish-task",
        help="Record task_finish, optional changeset evidence, handoff, and reservation updates",
    )
    finish_task_cmd.add_argument("workspace_id_or_uri")
    finish_task_cmd.add_argument("--agent-name", required=True)
    finish_task_cmd.add_argument("--summary", required=True)
    finish_task_cmd.add_argument("--intent")
    finish_task_cmd.add_argument("--changeset-file", dest="changeset_files", action="append")
    finish_task_cmd.add_argument("--git-commit")
    finish_task_cmd.add_argument("--branch")
    finish_task_cmd.add_argument("--to-agent")
    finish_task_cmd.add_argument("--next-step", dest="next_steps", action="append")
    finish_task_cmd.add_argument("--next-action")
    finish_task_cmd.add_argument("--blocked-on", dest="blocked_on", action="append")
    finish_task_cmd.add_argument("--tested-command", dest="tested_commands", action="append")
    finish_task_cmd.add_argument("--risk", dest="remaining_risks", action="append")
    reservation_mode = finish_task_cmd.add_mutually_exclusive_group()
    reservation_mode.add_argument(
        "--release-reservations",
        dest="reservation_mode",
        action="store_const",
        const="release",
    )
    reservation_mode.add_argument(
        "--renew-reservations",
        dest="reservation_mode",
        action="store_const",
        const="renew",
    )
    reservation_mode.add_argument(
        "--keep-reservations",
        dest="reservation_mode",
        action="store_const",
        const="keep",
    )
    finish_task_cmd.set_defaults(reservation_mode="keep")
    finish_task_cmd.add_argument("--ttl-minutes", type=int, default=120)
    finish_task_cmd.add_argument("--dry-run", action="store_true")
    finish_task_cmd.add_argument("--limit", type=int, default=50)
    finish_task_cmd.add_argument("--session-id")
    finish_task_cmd.add_argument("--session-external-id")
    finish_task_cmd.add_argument("--format", choices=["json", "markdown"], default="json")

    reserve_files_cmd = subparsers.add_parser("reserve-files", help="Reserve files for agent work")
    reserve_files_cmd.add_argument("workspace_id")
    reserve_files_cmd.add_argument("--agent-name", required=True)
    reserve_files_cmd.add_argument("--file", dest="files", action="append", required=True)
    reserve_files_cmd.add_argument("--purpose", default="")
    reserve_files_cmd.add_argument("--ttl-minutes", type=int, default=120)
    reserve_files_cmd.add_argument("--override-reason")

    release_file = subparsers.add_parser("release-reservation", help="Release a file reservation")
    release_file.add_argument("workspace_id")
    release_file_target = release_file.add_mutually_exclusive_group(required=True)
    release_file_target.add_argument("--reservation-id")
    release_file_target.add_argument("--file", dest="file_path")
    release_file.add_argument("--agent-name")

    cleanup_reservations = subparsers.add_parser(
        "cleanup-reservations", help="Mark expired file and symbol reservations as released"
    )
    cleanup_reservations.add_argument("--workspace-id")

    reserve_symbols_cmd = subparsers.add_parser(
        "reserve-symbols", help="Reserve code symbols for agent work"
    )
    reserve_symbols_cmd.add_argument("workspace_id")
    reserve_symbols_cmd.add_argument("--agent-name", required=True)
    reserve_symbols_cmd.add_argument("--symbol", dest="symbols", action="append", required=True)
    reserve_symbols_cmd.add_argument("--purpose", default="")
    reserve_symbols_cmd.add_argument("--ttl-minutes", type=int, default=120)
    reserve_symbols_cmd.add_argument("--override-reason")

    release_symbol = subparsers.add_parser("release-symbol", help="Release a symbol reservation")
    release_symbol.add_argument("workspace_id")
    release_symbol_target = release_symbol.add_mutually_exclusive_group(required=True)
    release_symbol_target.add_argument("--reservation-id")
    release_symbol_target.add_argument("--symbol")
    release_symbol.add_argument("--agent-name")

    renew_file = subparsers.add_parser("renew-reservation", help="Renew an active file reservation")
    renew_file.add_argument("workspace_id")
    renew_file_target = renew_file.add_mutually_exclusive_group(required=True)
    renew_file_target.add_argument("--reservation-id")
    renew_file_target.add_argument("--file", dest="file_path")
    renew_file.add_argument("--agent-name")
    renew_file.add_argument("--ttl-minutes", type=int, default=120)

    renew_symbol = subparsers.add_parser("renew-symbol", help="Renew an active symbol reservation")
    renew_symbol.add_argument("workspace_id")
    renew_symbol_target = renew_symbol.add_mutually_exclusive_group(required=True)
    renew_symbol_target.add_argument("--reservation-id")
    renew_symbol_target.add_argument("--symbol")
    renew_symbol.add_argument("--agent-name")
    renew_symbol.add_argument("--ttl-minutes", type=int, default=120)

    record_handoff = subparsers.add_parser(
        "record-handoff", help="Record a handoff summary for future agents"
    )
    record_handoff.add_argument("workspace_id")
    record_handoff.add_argument("--from-agent", required=True)
    record_handoff.add_argument("--to-agent")
    record_handoff.add_argument("--summary", required=True)
    record_handoff.add_argument("--next-step", dest="next_steps", action="append")
    record_handoff.add_argument("--next-action")
    record_handoff.add_argument("--blocked-on", dest="blocked_on", action="append")
    record_handoff.add_argument("--tested-command", dest="tested_commands", action="append")
    record_handoff.add_argument("--risk", dest="remaining_risks", action="append")
    record_handoff.add_argument("--template", default="standard")
    record_handoff.add_argument("--status", default="open")

    record_action = subparsers.add_parser(
        "record-agent-action",
        help="Record what an agent is doing for dashboard and lineage reads",
    )
    record_action.add_argument("workspace_id_or_uri")
    record_action.add_argument("--agent-name", required=True)
    record_action.add_argument("--action-type", "--action-kind", dest="action_type", required=True)
    record_action.add_argument("--summary", required=True)
    record_action.add_argument("--intent")
    record_action.add_argument("--status", default="recorded")
    record_action.add_argument("--session-id")
    record_action.add_argument("--session-external-id")

    list_handoffs = subparsers.add_parser("list-handoffs", help="List handoff summaries")
    list_handoffs.add_argument("--workspace-id-or-uri")
    list_handoffs.add_argument("--status")
    list_handoffs.add_argument("--limit", type=int, default=50)

    close_handoff = subparsers.add_parser("close-handoff", help="Close a handoff summary")
    close_handoff.add_argument("handoff_id")
    close_handoff.add_argument("--status", default="closed")

    reservation_events = subparsers.add_parser(
        "reservation-events", help="List reservation audit events"
    )
    reservation_events.add_argument("--workspace-id-or-uri")
    reservation_events.add_argument("--kind", choices=["file", "symbol"])
    reservation_events.add_argument(
        "--action",
        choices=["created", "renewed", "released", "expired"],
    )
    reservation_events.add_argument("--limit", type=int, default=50)

    benchmark = subparsers.add_parser(
        "benchmark-search", help="Measure retrieval latency for one or more queries"
    )
    benchmark.add_argument("queries", nargs="+")
    benchmark.add_argument("--mode", choices=["keyword", "vector", "hybrid"], default="keyword")
    benchmark.add_argument("--repeat", type=int, default=3)
    benchmark.add_argument("--limit", type=int, default=10)
    benchmark.add_argument("--workspace-uri")
    benchmark.add_argument("--source")
    benchmark.add_argument("--save", action="store_true")
    benchmark.add_argument("--label", default="")
    benchmark.add_argument("--judgments", type=Path)
    benchmark.add_argument("--include-results", action="store_true")
    benchmark.add_argument("--format", choices=["json", "markdown"], default="json")
    benchmark.add_argument("--rerank", choices=["none", "local", "api"], default="none")
    benchmark.add_argument("--candidate-limit", type=int)

    benchmark_report = subparsers.add_parser(
        "benchmark-report", help="Compare saved benchmark runs"
    )
    benchmark_report.add_argument("--workspace-uri")
    benchmark_report.add_argument("--mode")
    benchmark_report.add_argument("--limit", type=int, default=20)
    benchmark_report.add_argument("--format", choices=["json", "markdown"], default="json")

    record_changeset_cmd = subparsers.add_parser(
        "record-changeset",
        help="Record a changed file set and link it to indexed code entities",
    )
    record_changeset_cmd.add_argument("--workspace-uri", required=True)
    record_changeset_cmd.add_argument("--workspace-name", required=True)
    record_changeset_cmd.add_argument("--file", dest="files", action="append", required=True)
    record_changeset_cmd.add_argument("--status", default="modified")
    record_changeset_cmd.add_argument("--git-commit")
    record_changeset_cmd.add_argument("--branch")
    record_changeset_cmd.add_argument("--intent")
    record_changeset_cmd.add_argument("--summary", default="")
    record_changeset_cmd.add_argument("--session-id")
    record_changeset_cmd.add_argument("--session-external-id")
    record_changeset_cmd.add_argument(
        "--patch-file",
        type=Path,
        action="append",
        help="Unified diff patch file for the changed file; repeat to map by --file order",
    )

    explain_change_cmd = subparsers.add_parser(
        "explain-change",
        help="Show stored evidence (changesets, symbols, messages) for a file path",
    )
    explain_change_cmd.add_argument("file_path")
    explain_change_cmd.add_argument("--limit", type=int, default=10)
    explain_change_cmd.add_argument(
        "--narrative",
        action="store_true",
        help="Attach a short narrative summary that cites geond.evidence.v1 refs",
    )

    symbol_context_cmd = subparsers.add_parser(
        "symbol-context",
        help="Show code entities matching a symbol name and the changesets that touched them",
    )
    symbol_context_cmd.add_argument("symbol")
    symbol_context_cmd.add_argument("--limit", type=int, default=10)

    summarize_changeset_cmd = subparsers.add_parser(
        "summarize-changeset",
        help="Show one changeset (by UUID or git commit) with narrative + evidence refs",
    )
    summarize_changeset_cmd.add_argument("changeset_ref")
    summarize_changeset_cmd.add_argument(
        "--no-narrative",
        dest="narrative",
        action="store_false",
        default=True,
        help="Skip narrative synthesis and return only structured evidence",
    )

    args = parser.parse_args()

    if args.command == "doctor":
        report = collect_doctor_report(
            Path.cwd(),
            check_database=not args.skip_db,
            check_mcp=not args.skip_mcp,
        )
        if args.format == "text":
            print(format_doctor_report(report))
        else:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        if args.strict and report["status"] == "error":
            raise SystemExit(1)
        return

    if args.command == "mcp-smoke":
        try:
            report = run_stdio_smoke(
                command=args.server_command,
                args=args.server_arg,
                cwd=Path.cwd(),
                query=args.query,
                workspace_uri=args.workspace_uri,
                limit=args.limit,
                allow_empty_search=args.allow_empty_search,
            )
        except Exception as exc:
            report = {
                "status": "error",
                "server_command": args.server_command,
                "server_args": args.server_arg,
                "workspace_root": str(Path.cwd()),
                "checks": [
                    {
                        "name": "mcp_stdio_smoke",
                        "status": "error",
                        "message": f"MCP stdio smoke failed: {exc}",
                        "metadata": {"exception_type": type(exc).__name__},
                    }
                ],
                "server_log": "",
            }
        if args.format == "text":
            print(format_smoke_report(report))
        else:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        if args.strict and report["status"] != "ok":
            raise SystemExit(1)
        return

    if args.command == "install":
        result = install_clients(
            args.clients,
            repo_root=args.repo_root,
            workspace_root=args.workspace_root,
            config_path=args.config_path,
            server_name=args.server_name,
            database_url=args.database_url,
            database_profile=args.database_profile,
            privacy_mode=args.privacy_mode,
            embedding_provider=args.embedding_provider,
            embedding_model=args.embedding_model,
            write=args.write,
            overwrite=args.overwrite,
        )
        if args.format == "text":
            print(format_install_result_text(result))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "dashboard-overview":
        with connect(get_settings()) as conn:
            result = get_dashboard_overview(
                conn,
                args.workspace_id_or_uri,
                limit=args.limit,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return

    if args.command == "dashboard-events":
        with connect(get_settings()) as conn:
            result = get_agent_activity_events(
                conn,
                args.workspace_id_or_uri,
                limit=args.limit,
                event_kind=args.event_kind,
                agent_name=args.agent_name,
                status=args.status,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return

    if args.command == "dashboard-code-risk":
        with connect(get_settings()) as conn:
            result = get_dashboard_code_risk(
                conn,
                args.workspace_id_or_uri,
                limit=args.limit,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return

    if args.command == "dashboard-changesets":
        with connect(get_settings()) as conn:
            result = get_dashboard_changesets(
                conn,
                args.workspace_id_or_uri,
                limit=args.limit,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return

    if args.command == "dashboard-graph":
        with connect(get_settings()) as conn:
            result = get_workspace_lineage(
                conn,
                args.workspace_id_or_uri,
                limit=args.limit,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return

    if args.command == "dashboard" and args.dashboard_command == "serve":
        serve_dashboard(
            get_settings(),
            host=args.host,
            port=args.port,
            open_url=args.open,
        )
        return

    if args.command == "usage-summary":
        with connect(get_settings()) as conn:
            result = summarize_usage(
                conn,
                args.workspace_id_or_uri,
                source=args.source,
                provider=args.provider,
                model=args.model,
            )
        if args.format == "markdown":
            print(format_usage_summary_markdown(result))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return

    if args.command == "usage-by-agent":
        with connect(get_settings()) as conn:
            summary = summarize_usage(
                conn,
                args.workspace_id_or_uri,
                source=args.source,
                provider=args.provider,
                model=args.model,
            )
        result = usage_group_report(summary, "by_agent")
        if args.format == "markdown":
            print(
                format_usage_group_markdown(
                    result,
                    title="Usage By Agent",
                    group_key="by_agent",
                    label_keys=["agent_name"],
                )
            )
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return

    if args.command == "usage-by-model":
        with connect(get_settings()) as conn:
            summary = summarize_usage(
                conn,
                args.workspace_id_or_uri,
                source=args.source,
                provider=args.provider,
                model=args.model,
            )
        result = usage_group_report(summary, "by_model")
        if args.format == "markdown":
            print(
                format_usage_group_markdown(
                    result,
                    title="Usage By Model",
                    group_key="by_model",
                    label_keys=["provider", "model"],
                )
            )
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return

    if args.command == "usage-risk-signals":
        with connect(get_settings()) as conn:
            result = build_usage_risk_signals(get_dashboard_usage(conn, args.workspace_id_or_uri))
        if args.format == "markdown":
            print(format_usage_risk_signals_markdown(result))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return

    if args.command == "migrate":
        with connect(get_settings()) as conn:
            if args.all:
                schemas = discover_schema_files(args.schemas_dir)
                migrations = run_schema_migrations(conn, schemas)
                result = {
                    "status": "ok",
                    "schemas_dir": str(args.schemas_dir),
                    "migrations": migrations,
                }
            else:
                schemas = args.schemas or [Path("schemas/001_initial.sql")]
                for schema in schemas:
                    run_schema_file(conn, schema)
                result = {"status": "ok", "schemas": [str(schema) for schema in schemas]}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "benchmark-search":
        settings = get_settings()
        provider = get_embedding_provider(settings) if args.mode in {"vector", "hybrid"} else None
        judgments = load_judgments(args.judgments) if args.judgments else None
        with connect(settings) as conn:
            result = benchmark_search(
                conn,
                queries=args.queries,
                mode=args.mode,
                repeat=args.repeat,
                limit=args.limit,
                workspace_uri=args.workspace_uri,
                source=args.source,
                provider=provider,
                judgments=judgments,
                include_results=args.include_results,
                rerank=args.rerank,
                candidate_limit=args.candidate_limit,
            )
            if args.save:
                run_id = save_benchmark_run(
                    conn,
                    result,
                    label=args.label,
                    workspace_uri=args.workspace_uri,
                    provider=settings.embedding_provider if provider else None,
                    model=provider.model if provider else None,
                    metadata={"source": "cli"},
                )
                result["benchmark_run_id"] = run_id
        if args.format == "markdown":
            print(format_benchmark_result_markdown(result))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "benchmark-report":
        with connect(get_settings()) as conn:
            if args.format == "markdown" or not args.mode:
                result = compare_benchmark_runs(
                    conn,
                    workspace_uri=args.workspace_uri,
                    mode=args.mode,
                    limit=args.limit,
                )
            else:
                result = {
                    "runs": list_benchmark_runs(
                        conn,
                        workspace_uri=args.workspace_uri,
                        mode=args.mode,
                        limit=args.limit,
                    )
                }
        if args.format == "markdown":
            print(format_benchmark_report_markdown(result))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "record-changeset":
        with connect(get_settings()) as conn:
            workspace_id = upsert_workspace(
                conn,
                root_uri=args.workspace_uri,
                name=args.workspace_name,
                metadata={"source": "cli"},
            )
            result = record_changeset(
                conn,
                workspace_id=workspace_id,
                files=changed_files_from_args(args.files, args.status, args.patch_file),
                git_commit=args.git_commit,
                branch=args.branch,
                intent=args.intent,
                summary=args.summary,
                metadata={"source": "cli"},
                session_id=args.session_id,
                session_external_id=args.session_external_id,
            )
        print(
            json.dumps(
                {"status": "ok", "workspace_id": workspace_id, **result},
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.command == "record-agent-action":
        with connect(get_settings()) as conn:
            workspace_id = require_workspace_id(conn, args.workspace_id_or_uri)
            action_id = record_agent_action(
                conn,
                workspace_id=workspace_id,
                agent_name=args.agent_name,
                action_type=args.action_type,
                summary=args.summary,
                intent=args.intent,
                status=args.status,
                metadata={"source": "cli"},
                session_id=args.session_id,
                session_external_id=args.session_external_id,
            )
        print(
            json.dumps(
                {"status": "ok", "workspace_id": workspace_id, "action_id": action_id},
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.command == "parse-vscode":
        sessions = parse_storage(args.storage_path, args.session_id)
        summaries = [to_summary(session) for session in sessions]
        print(json.dumps(summaries, ensure_ascii=False, indent=2))
        return

    if args.command == "parse-codex":
        sessions = parse_codex_storage(args.storage_path, args.session_id, args.limit)
        summaries = [codex_to_summary(session) for session in sessions]
        print(json.dumps(summaries, ensure_ascii=False, indent=2))
        return

    if args.command == "parse-claude-code":
        sessions = parse_claude_code_storage(args.storage_path, args.session_id, args.limit)
        summaries = [claude_code_to_summary(session) for session in sessions]
        print(json.dumps(summaries, ensure_ascii=False, indent=2))
        return

    if args.command == "import-vscode":
        sessions = parse_storage(args.storage_path, args.session_id)
        with connect(get_settings()) as conn:
            workspace_id = upsert_workspace(
                conn,
                root_uri=args.workspace_uri,
                name=args.workspace_name,
                metadata={"source": "cli"},
            )
            stored = []
            usage_events = []
            for session in sessions:
                session_row_id = store_vscode_session(conn, workspace_id, session)
                stored.append(session_row_id)
                usage_events.extend(
                    record_vscode_copilot_usage_events(
                        conn,
                        workspace_id=workspace_id,
                        session=session,
                        session_row_id=session_row_id,
                    )
                )
        print(
            json.dumps(
                {
                    "status": "ok",
                    "workspace_id": workspace_id,
                    "imported_sessions": stored,
                    "imported_usage_events": usage_events,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.command == "import-codex":
        sessions = parse_codex_storage(args.storage_path, args.session_id, args.limit)
        with connect(get_settings()) as conn:
            workspace_id = upsert_workspace(
                conn,
                root_uri=args.workspace_uri,
                name=args.workspace_name,
                metadata={"source": "cli"},
            )
            stored = []
            usage_events = []
            for session in sessions:
                session_row_id = store_codex_session(conn, workspace_id, session)
                stored.append(session_row_id)
                usage_events.extend(
                    record_codex_usage_events(
                        conn,
                        workspace_id=workspace_id,
                        session=session,
                        session_row_id=session_row_id,
                    )
                )
        print(
            json.dumps(
                {
                    "status": "ok",
                    "workspace_id": workspace_id,
                    "imported_sessions": stored,
                    "imported_usage_events": usage_events,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.command == "import-claude-code":
        sessions = parse_claude_code_storage(args.storage_path, args.session_id, args.limit)
        imported: list[dict[str, str]] = []
        with connect(get_settings()) as conn:
            for session in sessions:
                workspace_uri = args.workspace_uri or workspace_uri_from_cwd(
                    session.metadata.get("cwd")
                )
                workspace_name = args.workspace_name or workspace_name_from_uri(workspace_uri)
                workspace_id = upsert_workspace(
                    conn,
                    root_uri=workspace_uri,
                    name=workspace_name,
                    metadata={"source": "cli", "import_source": "claude-code"},
                )
                session_row_id = store_claude_code_session(conn, workspace_id, session)
                usage_events = record_claude_code_usage_events(
                    conn,
                    workspace_id=workspace_id,
                    session=session,
                    session_row_id=session_row_id,
                )
                imported.append(
                    {
                        "workspace_id": workspace_id,
                        "session_id": session_row_id,
                        "external_id": session.session_id,
                        "usage_events": usage_events,
                    }
                )
        print(
            json.dumps(
                {"status": "ok", "imported_sessions": imported},
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.command == "import-manus-task":
        task_id = args.task_id or args.task_id_arg
        if not args.fixture and not task_id:
            print(
                json.dumps(
                    {"status": "error", "message": "Provide --task-id or --fixture"},
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            sys.exit(1)

        if not args.dry_run and not args.workspace_uri:
            print(
                json.dumps(
                    {
                        "status": "error",
                        "message": "Provide --workspace-uri (required unless --dry-run)",
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            sys.exit(1)

        if args.fixture:
            task = load_fixture(args.fixture, args.fixture_messages)
        else:
            try:
                client = ManusApiClient()
                task = client.fetch_task(task_id)
            except ManusApiError as exc:
                print(
                    json.dumps(
                        {
                            "status": "error",
                            "code": exc.status_code,
                            "endpoint": exc.endpoint,
                            "message": str(exc),
                        },
                        ensure_ascii=False,
                    ),
                    file=sys.stderr,
                )
                sys.exit(1)

        workspace_name = args.workspace_name or workspace_name_from_uri(args.workspace_uri)

        planned = {
            "task_id": task.task_id,
            "title": task.title,
            "status": task.status,
            "message_count": len(task.messages),
            "share_visibility": task.share_visibility,
            "workspace_uri": args.workspace_uri,
            "workspace_name": workspace_name,
        }

        if args.dry_run:
            print(
                json.dumps(
                    {"status": "dry-run", "planned": planned},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

        with connect(get_settings()) as conn:
            workspace_id = upsert_workspace(
                conn,
                root_uri=args.workspace_uri,
                name=workspace_name,
                metadata={"source": "cli", "import_source": "manus"},
            )
            session_row_id = store_manus_task(conn, workspace_id, task)

        print(
            json.dumps(
                {
                    "status": "ok",
                    "workspace_id": workspace_id,
                    "session_id": session_row_id,
                    "task_id": task.task_id,
                    "imported_messages": len(task.messages),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.command == "list-manus-tasks":
        try:
            client = ManusApiClient()
            resp = client.list_tasks(limit=args.limit, status=args.status)
        except ManusApiError as exc:
            print(
                json.dumps(
                    {
                        "status": "error",
                        "code": exc.status_code,
                        "endpoint": exc.endpoint,
                        "message": str(exc),
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            sys.exit(1)

        tasks = resp.get("tasks") or []
        show_private = getattr(args, "show_private_url", False)
        if args.format == "json":
            safe_tasks = [_mask_task_url(t, show_private) for t in tasks]
            print(
                json.dumps(
                    {"status": "ok", "count": len(safe_tasks), "tasks": safe_tasks},
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            )
        else:
            if not tasks:
                print("No tasks found.")
            else:
                header = f"{'TASK ID':<28}  {'STATUS':<12}  {'TITLE'}"
                print(header)
                print("-" * min(len(header) + 20, 100))
                for t in tasks:
                    tid = str(t.get("id") or t.get("task_id") or "")[:26]
                    status = str(t.get("status") or "")[:10]
                    title = str(t.get("title") or t.get("task_title") or "")[:60]
                    print(f"{tid:<28}  {status:<12}  {title}")
        return

    if args.command == "import-manus-tasks":
        try:
            client = ManusApiClient()
            resp = client.list_tasks(limit=args.limit, status=args.status)
        except ManusApiError as exc:
            print(
                json.dumps(
                    {
                        "status": "error",
                        "code": exc.status_code,
                        "endpoint": exc.endpoint,
                        "message": str(exc),
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            sys.exit(1)

        raw_tasks = resp.get("tasks") or []
        workspace_name = args.workspace_name or workspace_name_from_uri(args.workspace_uri)

        if args.dry_run:
            planned = [
                {
                    "task_id": str(t.get("id") or t.get("task_id") or ""),
                    "title": str(t.get("title") or t.get("task_title") or ""),
                    "status": str(t.get("status") or ""),
                }
                for t in raw_tasks
            ]
            print(
                json.dumps(
                    {"status": "dry-run", "count": len(planned), "tasks": planned},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

        results = []
        errors = []
        with connect(get_settings()) as conn:
            workspace_id = upsert_workspace(
                conn,
                root_uri=args.workspace_uri,
                name=workspace_name,
                metadata={"source": "cli", "import_source": "manus"},
            )
            for raw in raw_tasks:
                task_id = str(raw.get("id") or raw.get("task_id") or "")
                if not task_id:
                    continue
                try:
                    task = client.fetch_task(task_id, include_files=args.include_files)
                    session_row_id = store_manus_task(conn, workspace_id, task)
                    results.append(
                        {
                            "task_id": task_id,
                            "session_id": session_row_id,
                            "imported_messages": len(task.messages),
                            "imported_files": len(task.files),
                        }
                    )
                except ManusApiError as exc:
                    errors.append(
                        {
                            "task_id": task_id,
                            "code": exc.status_code,
                            "message": str(exc),
                        }
                    )

        print(
            json.dumps(
                {
                    "status": "ok",
                    "workspace_id": workspace_id,
                    "imported": len(results),
                    "errors": len(errors),
                    "tasks": results,
                    "error_details": errors,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.command == "manus-get-file":
        try:
            client = ManusApiClient()
            content = client.get_task_file_content(
                task_id=args.task_id,
                file_id=args.file_id,
                max_bytes=args.max_bytes,
            )
        except ManusApiError as exc:
            print(
                json.dumps(
                    {
                        "status": "error",
                        "code": exc.status_code,
                        "endpoint": exc.endpoint,
                        "message": str(exc),
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            sys.exit(1)
        if args.output:
            with open(args.output, "wb") as fh:
                fh.write(content)
            print(
                json.dumps(
                    {
                        "status": "ok",
                        "task_id": args.task_id,
                        "file_id": args.file_id,
                        "bytes_written": len(content),
                        "output": args.output,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            sys.stdout.buffer.write(content)
        return

    if args.command == "manus-dashboard":
        with connect(get_settings()) as conn:
            result = get_dashboard_manus_sessions(
                conn,
                workspace_id_or_uri=args.workspace_uri,
                limit=args.limit,
            )
        if args.format == "json":
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        else:
            tasks = result.get("tasks") or []
            if not tasks:
                print("No Manus tasks found.")
            else:
                header = f"{'TASK ID':<28}  {'STATUS':<12}  {'BLOCKED':<7}  {'MSGS':>4}  {'TITLE'}"
                print(header)
                print("-" * min(len(header) + 20, 110))
                for t in tasks:
                    tid = str(t.get("task_id") or "")[:26]
                    status = str(t.get("status") or "")[:10]
                    blocked = "yes" if t.get("is_blocked") else "no"
                    msgs = str(t.get("message_count") or 0)
                    title = str(t.get("title") or "")[:60]
                    print(f"{tid:<28}  {status:<12}  {blocked:<7}  {msgs:>4}  {title}")
        return

    if args.command == "manus-context-packet":
        with connect(get_settings()) as conn:
            packet = build_manus_context_packet(
                conn,
                workspace_uri=args.workspace_uri,
                query=args.query,
                limit=args.limit,
            )

        if args.create_task:
            try:
                client = ManusApiClient()
                prompt = _context_packet_to_prompt(packet)
                title = args.task_title or (
                    f"Geond context: {args.query[:60]}" if args.query else "Geond context review"
                )
                created = client.create_task(title=title, prompt=prompt)
                packet["created_manus_task"] = created
            except ManusApiError as exc:
                packet["create_task_error"] = {
                    "code": exc.status_code,
                    "message": str(exc),
                }

        print(json.dumps(packet, ensure_ascii=False, indent=2, default=str))
        return

    if args.command == "manus-task-contract":
        with connect(get_settings()) as conn:
            result = start_task(
                conn,
                workspace_id_or_uri=args.workspace_uri,
                agent_name=MANUS_AGENT_NAME,
                intent=args.intent,
                file_paths=args.files,
                symbols=args.symbols,
                reserve=args.reserve,
                ttl_minutes=args.ttl_minutes,
                override_reason=args.override_reason,
                dry_run=args.dry_run,
                limit=args.limit,
            )
            contract = _build_task_contract(
                result,
                intent=args.intent,
                expected_outputs=args.expected_outputs or [],
                validation_commands=args.validation_commands or [],
            )
        if args.format == "prompt":
            print(_contract_to_prompt(contract))
        else:
            print(json.dumps(contract, ensure_ascii=False, indent=2, default=str))
        return

    if args.command == "manus-task-complete":
        task_id = args.task_id or args.task_id_arg
        if not args.fixture and not task_id:
            print(
                json.dumps(
                    {"status": "error", "message": "Provide --task-id or --fixture"},
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            sys.exit(1)

        if args.fixture:
            task = load_fixture(args.fixture, args.fixture_messages)
        else:
            try:
                client = ManusApiClient()
                task = client.fetch_task(task_id)
            except ManusApiError as exc:
                print(
                    json.dumps(
                        {
                            "status": "error",
                            "code": exc.status_code,
                            "message": str(exc),
                        },
                        ensure_ascii=False,
                    ),
                    file=sys.stderr,
                )
                sys.exit(1)

        workspace_name = args.workspace_name or workspace_name_from_uri(args.workspace_uri)
        handoff_summary = args.handoff_summary or f"Manus task {task.task_id}: {task.title}"
        session_row_id = None

        if not args.dry_run:
            with connect(get_settings()) as conn:
                workspace_id = upsert_workspace(
                    conn,
                    root_uri=args.workspace_uri,
                    name=workspace_name,
                    metadata={"source": "cli", "import_source": "manus"},
                )
                session_row_id = store_manus_task(conn, workspace_id, task)
                finish_result = finish_task(
                    conn,
                    workspace_id_or_uri=args.workspace_uri,
                    agent_name=MANUS_AGENT_NAME,
                    summary=handoff_summary,
                    next_steps=args.next_steps,
                    tested_commands=args.tested_commands,
                    remaining_risks=args.remaining_risks,
                    next_action=args.next_action or None,
                    to_agent_name=args.to_agent or None,
                    reservation_mode=args.reservation_mode,
                    dry_run=False,
                    session_id=session_row_id,
                )
        else:
            finish_result = {"status": "dry_run"}

        print(
            json.dumps(
                {
                    "status": "dry-run" if args.dry_run else "ok",
                    "task_id": task.task_id,
                    "session_id": session_row_id,
                    "imported_messages": len(task.messages),
                    "finish": finish_result,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        return

    if args.command == "embed-messages":
        settings = get_settings()
        provider = get_embedding_provider(settings)
        with connect(settings) as conn:
            embedded = embed_pending_messages(
                conn,
                provider=provider,
                limit=args.limit,
                batch_size=args.batch_size,
            )
            stats = embedding_stats(conn)
        print(
            json.dumps(
                {"status": "ok", "embedded": embedded, "stats": stats},
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.command == "search":
        settings = get_settings()
        with connect(settings) as conn:
            if args.mode == "keyword":
                results = search_dev_memory(
                    conn,
                    args.query,
                    args.limit,
                    workspace_uri=args.workspace_uri,
                    source=args.source,
                    rerank=args.rerank,
                    candidate_limit=args.candidate_limit,
                )
            else:
                provider = get_embedding_provider(settings)
                query_vector = provider.embed([args.query])[0]
                if args.mode == "vector":
                    results = vector_search_dev_memory(
                        conn,
                        query_vector=query_vector,
                        model=provider.model,
                        limit=args.limit,
                        workspace_uri=args.workspace_uri,
                        source=args.source,
                        query=args.query,
                        rerank=args.rerank,
                        candidate_limit=args.candidate_limit,
                    )
                else:
                    results = hybrid_search_dev_memory(
                        conn,
                        query=args.query,
                        query_vector=query_vector,
                        model=provider.model,
                        limit=args.limit,
                        workspace_uri=args.workspace_uri,
                        source=args.source,
                        rerank=args.rerank,
                        candidate_limit=args.candidate_limit,
                    )
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return

    if args.command == "index-python":
        root_path = args.root or args.path
        indexed_files = index_python_path(args.path, root_path=root_path)
        with connect(get_settings()) as conn:
            workspace_id = upsert_workspace(
                conn,
                root_uri=args.workspace_uri,
                name=args.workspace_name,
                metadata={"source": "cli"},
            )
            stats = store_code_index(conn, workspace_id, indexed_files)
        print(
            json.dumps(
                {"status": "ok", "workspace_id": workspace_id, **stats},
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.command == "index-ts-js":
        root_path = args.root or args.path
        indexed_files = index_ts_js_path(args.path, root_path=root_path)
        with connect(get_settings()) as conn:
            workspace_id = upsert_workspace(
                conn,
                root_uri=args.workspace_uri,
                name=args.workspace_name,
                metadata={"source": "cli"},
            )
            stats = store_code_index(conn, workspace_id, indexed_files)
        print(
            json.dumps(
                {"status": "ok", "workspace_id": workspace_id, **stats},
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.command == "index-tree-sitter":
        root_path = args.root or args.path
        indexed_files = index_tree_sitter_path(args.path, root_path=root_path)
        with connect(get_settings()) as conn:
            workspace_id = upsert_workspace(
                conn,
                root_uri=args.workspace_uri,
                name=args.workspace_name,
                metadata={"source": "cli"},
            )
            stats = store_code_index(conn, workspace_id, indexed_files)
        print(
            json.dumps(
                {"status": "ok", "workspace_id": workspace_id, **stats},
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.command == "import-lsp-references":
        data = json.loads(args.path.read_text(encoding="utf-8"))
        references = normalize_lsp_references(
            data,
            workspace_root=args.workspace_root,
            target_qualified_name=args.target_qualified_name,
            provider=args.provider,
        )
        if not isinstance(references, list):
            raise SystemExit("LSP reference JSON must be a list or an object with references")
        with connect(get_settings()) as conn:
            workspace_id = require_workspace_id(conn, args.workspace_id)
            result = store_lsp_references(
                conn,
                workspace_id=workspace_id,
                references=references,
                replace=not args.append,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "normalize-lsp-references":
        data = json.loads(args.path.read_text(encoding="utf-8"))
        result = {
            "references": normalize_lsp_references(
                data,
                workspace_root=args.workspace_root,
                target_qualified_name=args.target_qualified_name,
                provider=args.provider,
            )
        }
        output = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            args.output.write_text(output + "\n", encoding="utf-8")
        else:
            print(output)
        return

    if args.command == "collect-lsp-references":
        server_command, server_profile = resolve_lsp_server_command(
            args.server_command,
            args.server_profile,
            args.path,
        )
        payload = collect_lsp_references(
            server_command,
            workspace_root=(args.workspace_root or Path.cwd()),
            file_path=args.path,
            line=args.line,
            character=args.character,
            target_qualified_name=args.target_qualified_name,
            provider=args.provider,
            language_id=args.language_id,
            server_profile=server_profile,
            timeout_seconds=args.timeout_seconds,
            include_declaration=args.include_declaration,
        )
        if args.output:
            args.output.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        if args.import_workspace_id:
            references = normalize_lsp_references(payload)
            with connect(get_settings()) as conn:
                workspace_id = require_workspace_id(conn, args.import_workspace_id)
                import_result = store_lsp_references(
                    conn,
                    workspace_id=workspace_id,
                    references=references,
                    replace=not args.append,
                )
            print(
                json.dumps(
                    {
                        "status": "ok",
                        "workspace_id": workspace_id,
                        "locations": len(payload.get("locations", [])),
                        "import_result": import_result,
                        "output": str(args.output) if args.output else None,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.output:
            print(
                json.dumps(
                    {
                        "status": "ok",
                        "locations": len(payload.get("locations", [])),
                        "output": str(args.output),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if args.command == "lsp-server-profiles":
        print(json.dumps(list_lsp_server_profiles(), ensure_ascii=False, indent=2))
        return

    if args.command == "seed-sample":
        with connect(get_settings()) as conn:
            run_schema_file(conn, args.schema)
            result = seed_sample_workspace(conn)
        print(json.dumps({"status": "ok", **result}, ensure_ascii=False, indent=2))
        return

    if args.command == "purge-workspace":
        if not args.yes:
            raise SystemExit("Refusing to purge without --yes")
        with connect(get_settings()) as conn:
            result = purge_workspace(conn, args.workspace_id_or_uri)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "register-workspace-alias":
        with connect(get_settings()) as conn:
            result = register_workspace_alias(
                conn,
                workspace_id_or_uri=args.workspace_id_or_uri,
                alias_uri=args.alias_uri,
                reason=args.reason,
                metadata={"source": "cli"},
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "workspace-aliases":
        with connect(get_settings()) as conn:
            result = list_workspace_aliases(conn, args.workspace_id_or_uri)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "workspace-policy":
        with connect(get_settings()) as conn:
            if args.reservation_conflict_policy:
                result = set_workspace_coordination_policy(
                    conn,
                    args.workspace_id_or_uri,
                    reservation_conflict_policy=args.reservation_conflict_policy,
                )
            else:
                result = get_workspace_coordination_policy(conn, args.workspace_id_or_uri)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "fingerprint-workspace":
        fingerprints = discover_workspace_fingerprints(args.path_or_uri)
        fingerprints.extend(fingerprint_from_arg(value) for value in args.fingerprint or [])
        with connect(get_settings()) as conn:
            recorded = record_workspace_fingerprints(
                conn,
                workspace_id_or_uri=args.workspace_id_or_uri,
                fingerprints=fingerprints,
            )
        print(
            json.dumps(
                {"status": "ok", "fingerprints": recorded},
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.command == "suggest-workspace-aliases":
        alias_uri = workspace_uri_from_path_or_uri(args.path_or_uri)
        fingerprints = discover_workspace_fingerprints(args.path_or_uri)
        fingerprints.extend(fingerprint_from_arg(value) for value in args.fingerprint or [])
        with connect(get_settings()) as conn:
            suggestions = suggest_workspace_aliases(conn, alias_uri, fingerprints)
            registered = None
            eligible = [
                item
                for item in suggestions
                if not item["already_resolves"] and item["confidence"] >= args.min_confidence
            ]
            if args.register_best and len(eligible) == 1:
                registered = register_workspace_alias(
                    conn,
                    workspace_id_or_uri=eligible[0]["workspace_id"],
                    alias_uri=alias_uri,
                    reason="fingerprint-match",
                    metadata={"source": "cli", "confidence": eligible[0]["confidence"]},
                )
        print(
            json.dumps(
                {
                    "alias_uri": alias_uri,
                    "fingerprints": fingerprints,
                    "suggestions": suggestions,
                    "registered": registered,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.command == "conflicts":
        with connect(get_settings()) as conn:
            workspace_id = require_workspace_id(conn, args.workspace_id)
            result = {
                "file_reservations": list_active_file_reservations(
                    conn,
                    workspace_id,
                    args.files,
                ),
                "symbol_reservations": list_active_symbol_reservations(
                    conn,
                    workspace_id,
                    args.symbols,
                ),
            }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "review-context":
        with connect(get_settings()) as conn:
            result = review_workspace_context(
                conn,
                workspace_id_or_uri=args.workspace_id_or_uri,
                intent=args.intent,
                file_paths=args.files,
                symbols=args.symbols,
                agent_name=args.agent_name,
                limit=args.limit,
            )
        if args.format == "markdown":
            print(format_context_review_markdown(result))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "start-task":
        with connect(get_settings()) as conn:
            result = start_task(
                conn,
                workspace_id_or_uri=args.workspace_id_or_uri,
                agent_name=args.agent_name,
                intent=args.intent,
                file_paths=args.files,
                symbols=args.symbols,
                reserve=args.reserve,
                ttl_minutes=args.ttl_minutes,
                override_reason=args.override_reason,
                dry_run=args.dry_run,
                limit=args.limit,
                session_id=args.session_id,
                session_external_id=args.session_external_id,
            )
        if args.format == "markdown":
            print(format_task_result_markdown(result))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return

    if args.command == "finish-task":
        with connect(get_settings()) as conn:
            result = finish_task(
                conn,
                workspace_id_or_uri=args.workspace_id_or_uri,
                agent_name=args.agent_name,
                summary=args.summary,
                intent=args.intent,
                changed_files=parse_changed_files(args.changeset_files),
                git_commit=args.git_commit,
                branch=args.branch,
                to_agent_name=args.to_agent,
                next_steps=args.next_steps,
                next_action=args.next_action,
                blocked_on=args.blocked_on,
                tested_commands=args.tested_commands,
                remaining_risks=args.remaining_risks,
                reservation_mode=args.reservation_mode,
                ttl_minutes=args.ttl_minutes,
                dry_run=args.dry_run,
                limit=args.limit,
                session_id=args.session_id,
                session_external_id=args.session_external_id,
            )
        if args.format == "markdown":
            print(format_task_result_markdown(result))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return

    if args.command == "reserve-files":
        with connect(get_settings()) as conn:
            workspace_id = require_workspace_id(conn, args.workspace_id)
            result = reserve_files(
                conn,
                workspace_id=workspace_id,
                agent_name=args.agent_name,
                file_paths=args.files,
                purpose=args.purpose,
                ttl_minutes=args.ttl_minutes,
                override_reason=args.override_reason,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "release-reservation":
        with connect(get_settings()) as conn:
            workspace_id = require_workspace_id(conn, args.workspace_id)
            released = release_reservation(
                conn,
                workspace_id=workspace_id,
                reservation_id=args.reservation_id,
                file_path=args.file_path,
                agent_name=args.agent_name,
            )
        print(json.dumps({"released": released}, ensure_ascii=False, indent=2))
        return

    if args.command == "cleanup-reservations":
        with connect(get_settings()) as conn:
            workspace_id = (
                require_workspace_id(conn, args.workspace_id) if args.workspace_id else None
            )
            result = cleanup_expired_reservations_for_workspace(conn, workspace_id)
        print(json.dumps({"status": "ok", **result}, ensure_ascii=False, indent=2))
        return

    if args.command == "reserve-symbols":
        with connect(get_settings()) as conn:
            workspace_id = require_workspace_id(conn, args.workspace_id)
            result = reserve_symbols(
                conn,
                workspace_id=workspace_id,
                agent_name=args.agent_name,
                symbols=args.symbols,
                purpose=args.purpose,
                ttl_minutes=args.ttl_minutes,
                override_reason=args.override_reason,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "release-symbol":
        with connect(get_settings()) as conn:
            workspace_id = require_workspace_id(conn, args.workspace_id)
            released = release_symbol_reservation(
                conn,
                workspace_id=workspace_id,
                reservation_id=args.reservation_id,
                symbol=args.symbol,
                agent_name=args.agent_name,
            )
        print(json.dumps({"released": released}, ensure_ascii=False, indent=2))
        return

    if args.command == "renew-reservation":
        with connect(get_settings()) as conn:
            workspace_id = require_workspace_id(conn, args.workspace_id)
            renewed = renew_reservation(
                conn,
                workspace_id=workspace_id,
                reservation_id=args.reservation_id,
                file_path=args.file_path,
                agent_name=args.agent_name,
                ttl_minutes=args.ttl_minutes,
            )
        print(json.dumps({"renewed": renewed}, ensure_ascii=False, indent=2))
        return

    if args.command == "renew-symbol":
        with connect(get_settings()) as conn:
            workspace_id = require_workspace_id(conn, args.workspace_id)
            renewed = renew_symbol_reservation(
                conn,
                workspace_id=workspace_id,
                reservation_id=args.reservation_id,
                symbol=args.symbol,
                agent_name=args.agent_name,
                ttl_minutes=args.ttl_minutes,
            )
        print(json.dumps({"renewed": renewed}, ensure_ascii=False, indent=2))
        return

    if args.command == "record-handoff":
        with connect(get_settings()) as conn:
            workspace_id = require_workspace_id(conn, args.workspace_id)
            handoff_id = record_handoff_summary(
                conn,
                workspace_id=workspace_id,
                from_agent_name=args.from_agent,
                to_agent_name=args.to_agent,
                summary=args.summary,
                next_steps=args.next_steps,
                blocked_on=args.blocked_on,
                status=args.status,
                tested_commands=args.tested_commands,
                remaining_risks=args.remaining_risks,
                next_action=args.next_action,
                template=args.template,
            )
        print(json.dumps({"handoff_id": handoff_id}, ensure_ascii=False, indent=2))
        return

    if args.command == "list-handoffs":
        with connect(get_settings()) as conn:
            result = list_handoff_summaries(
                conn,
                workspace_id_or_uri=args.workspace_id_or_uri,
                status=args.status,
                limit=args.limit,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "close-handoff":
        with connect(get_settings()) as conn:
            closed = close_handoff_summary(conn, args.handoff_id, status=args.status)
        print(json.dumps({"closed": closed}, ensure_ascii=False, indent=2))
        return

    if args.command == "reservation-events":
        with connect(get_settings()) as conn:
            result = list_reservation_events(
                conn,
                workspace_id_or_uri=args.workspace_id_or_uri,
                reservation_kind=args.kind,
                action=args.action,
                limit=args.limit,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "explain-change":
        settings = get_settings()
        with connect(settings) as conn:
            result = explain_change(
                conn,
                args.file_path,
                limit=args.limit,
                include_narrative=args.narrative,
                settings=settings,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return

    if args.command == "symbol-context":
        with connect(get_settings()) as conn:
            result = get_symbol_context(conn, args.symbol, limit=args.limit)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return

    if args.command == "summarize-changeset":
        settings = get_settings()
        with connect(settings) as conn:
            result = get_changeset_detail(
                conn,
                args.changeset_ref,
                include_narrative=args.narrative,
                settings=settings,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return


if __name__ == "__main__":
    main()
