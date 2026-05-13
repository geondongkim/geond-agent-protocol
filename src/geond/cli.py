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
from geond.adapters.vscode_copilot import parse_storage, to_summary
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
from geond.db import connect, run_schema_file
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
from geond.storage.dashboard import get_agent_activity_events, get_dashboard_overview
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
    store_vscode_session,
    suggest_workspace_aliases,
    upsert_workspace,
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


def main() -> None:
    configure_cli_output()
    parser = argparse.ArgumentParser(prog="geond")
    subparsers = parser.add_subparsers(dest="command", required=True)

    migrate = subparsers.add_parser("migrate", help="Apply a SQL schema file")
    migrate.add_argument("--schema", type=Path, default=Path("schemas/001_initial.sql"))

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

    dashboard = subparsers.add_parser("dashboard", help="Run local dashboard commands")
    dashboard_subparsers = dashboard.add_subparsers(dest="dashboard_command", required=True)
    dashboard_serve = dashboard_subparsers.add_parser(
        "serve",
        help="Serve the read-only local dashboard API",
    )
    dashboard_serve.add_argument("--host", default="127.0.0.1")
    dashboard_serve.add_argument("--port", type=int, default=8765)
    dashboard_serve.add_argument("--open", action="store_true", help="Open the dashboard URL")

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

    if args.command == "migrate":
        with connect(get_settings()) as conn:
            run_schema_file(conn, args.schema)
        print(json.dumps({"status": "ok", "schema": str(args.schema)}, ensure_ascii=False))
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
            )
        print(
            json.dumps(
                {"status": "ok", "workspace_id": workspace_id, **result},
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
            stored = [store_vscode_session(conn, workspace_id, session) for session in sessions]
        print(
            json.dumps(
                {"status": "ok", "workspace_id": workspace_id, "imported_sessions": stored},
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
            stored = [store_codex_session(conn, workspace_id, session) for session in sessions]
        print(
            json.dumps(
                {"status": "ok", "workspace_id": workspace_id, "imported_sessions": stored},
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
                imported.append(
                    {
                        "workspace_id": workspace_id,
                        "session_id": session_row_id,
                        "external_id": session.session_id,
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
