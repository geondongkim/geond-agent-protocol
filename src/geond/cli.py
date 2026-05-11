from __future__ import annotations

import argparse
import json
from pathlib import Path

from geond.adapters.vscode_copilot import parse_storage, to_summary
from geond.config import get_settings
from geond.db import connect, run_schema_file
from geond.embeddings import get_embedding_provider
from geond.retrieval.simple import (
    hybrid_search_dev_memory,
    search_dev_memory,
    vector_search_dev_memory,
)
from geond.storage.embeddings import embed_pending_messages, embedding_stats
from geond.storage.repository import store_vscode_session, upsert_workspace


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

    import_vscode = subparsers.add_parser(
        "import-vscode", help="Import VS Code Copilot Chat storage into Geond DB"
    )
    import_vscode.add_argument("storage_path", type=Path)
    import_vscode.add_argument("--session-id")
    import_vscode.add_argument("--workspace-uri", required=True)
    import_vscode.add_argument("--workspace-name", required=True)

    embed_messages = subparsers.add_parser(
        "embed-messages", help="Create embeddings for imported message records"
    )
    embed_messages.add_argument("--limit", type=int, default=100)
    embed_messages.add_argument("--batch-size", type=int, default=32)

    search = subparsers.add_parser("search", help="Search imported development memory")
    search.add_argument("query")
    search.add_argument("--mode", choices=["keyword", "vector", "hybrid"], default="hybrid")
    search.add_argument("--limit", type=int, default=10)

    args = parser.parse_args()

    if args.command == "migrate":
        with connect(get_settings()) as conn:
            run_schema_file(conn, args.schema)
        print(json.dumps({"status": "ok", "schema": str(args.schema)}, ensure_ascii=False))
        return

    if args.command == "parse-vscode":
        sessions = parse_storage(args.storage_path, args.session_id)
        print(json.dumps([to_summary(session) for session in sessions], ensure_ascii=False, indent=2))
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
                results = search_dev_memory(conn, args.query, args.limit)
            else:
                provider = get_embedding_provider(settings)
                query_vector = provider.embed([args.query])[0]
                if args.mode == "vector":
                    results = vector_search_dev_memory(
                        conn,
                        query_vector=query_vector,
                        model=provider.model,
                        limit=args.limit,
                    )
                else:
                    results = hybrid_search_dev_memory(
                        conn,
                        query=args.query,
                        query_vector=query_vector,
                        model=provider.model,
                        limit=args.limit,
                    )
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return


if __name__ == "__main__":
    main()
