from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from geond import orchestrator
from geond.config import get_settings
from geond.db import connect


def main() -> None:
    parser = argparse.ArgumentParser(prog="geond-orchestrator")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_cmd = subparsers.add_parser("run", help="Create a claim-mode orchestrator run")
    run_cmd.add_argument("goal")
    run_cmd.add_argument("--workspace", required=True)
    run_cmd.add_argument("--risk-level", default="medium")
    run_cmd.add_argument("--task-graph", type=Path)
    run_cmd.add_argument("--format", choices=["markdown", "json"], default="markdown")

    status_cmd = subparsers.add_parser("status", help="Read orchestrator run status")
    status_cmd.add_argument("run_id")
    status_cmd.add_argument(
        "--base-dir",
        type=Path,
        default=orchestrator.DEFAULT_MANIFEST_BASE_DIR,
    )
    status_cmd.add_argument("--format", choices=["markdown", "json"], default="markdown")

    dispatch_cmd = subparsers.add_parser("dispatch", help="Dispatch claim-mode or spawn-mode work")
    dispatch_cmd.add_argument("--run", dest="run_id", required=True)
    dispatch_cmd.add_argument("--mode", choices=["claim", "spawn"], default="claim")
    dispatch_cmd.add_argument("--agent", default="codex")
    dispatch_cmd.add_argument("--execute", action="store_true")
    dispatch_cmd.add_argument("--task", dest="task_id")
    dispatch_cmd.add_argument("--model")
    dispatch_cmd.add_argument("--sandbox", default="workspace-write")
    dispatch_cmd.add_argument("--timeout-seconds", type=int, default=3600)
    dispatch_cmd.add_argument("--write-bundle", action="store_true")
    dispatch_cmd.add_argument(
        "--base-dir",
        type=Path,
        default=orchestrator.DEFAULT_MANIFEST_BASE_DIR,
    )
    dispatch_cmd.add_argument("--format", choices=["markdown", "json"], default="markdown")

    resume_cmd = subparsers.add_parser("resume", help="Resume and summarize an existing run")
    resume_cmd.add_argument("run_id")
    resume_cmd.add_argument(
        "--base-dir",
        type=Path,
        default=orchestrator.DEFAULT_MANIFEST_BASE_DIR,
    )
    resume_cmd.add_argument("--format", choices=["markdown", "json"], default="markdown")

    finalize_cmd = subparsers.add_parser("finalize", help="Finalize a ready run")
    finalize_cmd.add_argument("run_id")
    finalize_cmd.add_argument("--write-manifest", action="store_true")
    finalize_cmd.add_argument(
        "--base-dir",
        type=Path,
        default=orchestrator.DEFAULT_MANIFEST_BASE_DIR,
    )
    finalize_cmd.add_argument("--format", choices=["markdown", "json"], default="markdown")

    args = parser.parse_args()

    with connect(get_settings()) as conn:
        if args.command == "run":
            payload = orchestrator.start_run(
                conn,
                goal=args.goal,
                workspace_id_or_uri=args.workspace,
                risk_level=args.risk_level,
                task_graph_path=args.task_graph,
            )
        elif args.command == "status":
            payload = orchestrator.get_status(conn, args.run_id, manifest_base_dir=args.base_dir)
        elif args.command == "dispatch":
            if args.mode == "claim":
                payload = orchestrator.dispatch_claim(
                    conn,
                    run_id=args.run_id,
                    agent_name=args.agent,
                    manifest_base_dir=args.base_dir,
                )
            else:
                payload = orchestrator.dispatch_spawn(
                    conn,
                    run_id=args.run_id,
                    agent_name=args.agent,
                    execute=args.execute,
                    task_id=args.task_id,
                    model=args.model,
                    sandbox=args.sandbox,
                    timeout_seconds=args.timeout_seconds,
                    write_bundle=args.write_bundle,
                    manifest_base_dir=args.base_dir,
                )
        elif args.command == "resume":
            payload = orchestrator.resume_run(
                conn,
                args.run_id,
                manifest_base_dir=args.base_dir,
            )
        elif args.command == "finalize":
            payload = orchestrator.finalize_run(
                conn,
                args.run_id,
                write_manifest=args.write_manifest,
                manifest_base_dir=args.base_dir,
            )
        else:
            parser.error(f"Unsupported command: {args.command}")

    print_payload(payload, args.format)


def print_payload(payload: dict[str, Any], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return
    markdown = payload.get("markdown")
    if markdown:
        print(markdown, end="")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
