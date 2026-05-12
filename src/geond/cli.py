from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from geond.adapters.claude_code import parse_storage as parse_claude_code_storage
from geond.adapters.claude_code import to_summary as claude_code_to_summary
from geond.adapters.codex import parse_storage as parse_codex_storage
from geond.adapters.codex import to_summary as codex_to_summary
from geond.adapters.vscode_copilot import parse_storage, to_summary
from geond.code_graph.python_indexer import index_python_path
from geond.code_graph.tree_sitter_indexer import index_tree_sitter_path
from geond.code_graph.ts_js_indexer import index_ts_js_path
from geond.config import get_settings
from geond.db import connect, run_schema_file
from geond.embeddings import get_embedding_provider
from geond.retrieval.simple import (
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
from geond.storage.code_graph import store_code_index
from geond.storage.embeddings import embed_pending_messages, embedding_stats
from geond.storage.maintenance import purge_workspace, seed_sample_workspace
from geond.storage.repository import (
    cleanup_expired_reservations_for_workspace,
    list_active_file_reservations,
    list_active_symbol_reservations,
    list_handoff_summaries,
    record_changeset,
    record_handoff_summary,
    release_symbol_reservation,
    reserve_symbols,
    store_claude_code_session,
    store_codex_session,
    store_vscode_session,
    upsert_workspace,
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


def main() -> None:
    parser = argparse.ArgumentParser(prog="geond")
    subparsers = parser.add_subparsers(dest="command", required=True)

    migrate = subparsers.add_parser("migrate", help="Apply a SQL schema file")
    migrate.add_argument("--schema", type=Path, default=Path("schemas/001_initial.sql"))

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

    seed_sample = subparsers.add_parser(
        "seed-sample", help="Insert a small sample workspace and session"
    )
    seed_sample.add_argument("--schema", type=Path, default=Path("schemas/001_initial.sql"))

    purge = subparsers.add_parser("purge-workspace", help="Delete one workspace and its data")
    purge.add_argument("workspace_id_or_uri")
    purge.add_argument("--yes", action="store_true", help="Confirm deletion")

    conflicts = subparsers.add_parser("conflicts", help="List active file and symbol reservations")
    conflicts.add_argument("workspace_id")
    conflicts.add_argument("--file", dest="files", action="append")
    conflicts.add_argument("--symbol", dest="symbols", action="append")

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

    release_symbol = subparsers.add_parser("release-symbol", help="Release a symbol reservation")
    release_symbol.add_argument("workspace_id")
    release_symbol_target = release_symbol.add_mutually_exclusive_group(required=True)
    release_symbol_target.add_argument("--reservation-id")
    release_symbol_target.add_argument("--symbol")
    release_symbol.add_argument("--agent-name")

    record_handoff = subparsers.add_parser(
        "record-handoff", help="Record a handoff summary for future agents"
    )
    record_handoff.add_argument("workspace_id")
    record_handoff.add_argument("--from-agent", required=True)
    record_handoff.add_argument("--to-agent")
    record_handoff.add_argument("--summary", required=True)
    record_handoff.add_argument("--next-step", dest="next_steps", action="append")
    record_handoff.add_argument("--blocked-on", dest="blocked_on", action="append")
    record_handoff.add_argument("--status", default="open")

    list_handoffs = subparsers.add_parser("list-handoffs", help="List handoff summaries")
    list_handoffs.add_argument("--workspace-id-or-uri")
    list_handoffs.add_argument("--status")
    list_handoffs.add_argument("--limit", type=int, default=50)

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
    args = parser.parse_args()

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

    if args.command == "conflicts":
        with connect(get_settings()) as conn:
            result = {
                "file_reservations": list_active_file_reservations(
                    conn,
                    args.workspace_id,
                    args.files,
                ),
                "symbol_reservations": list_active_symbol_reservations(
                    conn,
                    args.workspace_id,
                    args.symbols,
                ),
            }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "cleanup-reservations":
        with connect(get_settings()) as conn:
            result = cleanup_expired_reservations_for_workspace(conn, args.workspace_id)
        print(json.dumps({"status": "ok", **result}, ensure_ascii=False, indent=2))
        return

    if args.command == "reserve-symbols":
        with connect(get_settings()) as conn:
            result = reserve_symbols(
                conn,
                workspace_id=args.workspace_id,
                agent_name=args.agent_name,
                symbols=args.symbols,
                purpose=args.purpose,
                ttl_minutes=args.ttl_minutes,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "release-symbol":
        with connect(get_settings()) as conn:
            released = release_symbol_reservation(
                conn,
                workspace_id=args.workspace_id,
                reservation_id=args.reservation_id,
                symbol=args.symbol,
                agent_name=args.agent_name,
            )
        print(json.dumps({"released": released}, ensure_ascii=False, indent=2))
        return

    if args.command == "record-handoff":
        with connect(get_settings()) as conn:
            handoff_id = record_handoff_summary(
                conn,
                workspace_id=args.workspace_id,
                from_agent_name=args.from_agent,
                to_agent_name=args.to_agent,
                summary=args.summary,
                next_steps=args.next_steps,
                blocked_on=args.blocked_on,
                status=args.status,
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


if __name__ == "__main__":
    main()
