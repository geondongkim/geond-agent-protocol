from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from geond.config import get_settings
from geond.db import connect
from geond import orchestrator


def main() -> None:
    parser = argparse.ArgumentParser(prog="geond-orchestrator")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_cmd = subparsers.add_parser("run", help="Create a claim-mode orchestrator run")
    run_cmd.add_argument("goal")
    run_cmd.add_argument("--workspace", required=True)
    run_cmd.add_argument("--risk-level", default="medium")
    run_cmd.add_argument("--format", choices=["markdown", "json"], default="markdown")

    status_cmd = subparsers.add_parser("status", help="Read orchestrator run status")
    status_cmd.add_argument("run_id")
    status_cmd.add_argument("--format", choices=["markdown", "json"], default="markdown")

    dispatch_cmd = subparsers.add_parser("dispatch", help="Print claim-mode worker commands")
    dispatch_cmd.add_argument("--run", dest="run_id", required=True)
    dispatch_cmd.add_argument("--mode", choices=["claim"], default="claim")
    dispatch_cmd.add_argument("--agent", default="codex")
    dispatch_cmd.add_argument("--format", choices=["markdown", "json"], default="markdown")

    resume_cmd = subparsers.add_parser("resume", help="Resume and summarize an existing run")
    resume_cmd.add_argument("run_id")
    resume_cmd.add_argument("--format", choices=["markdown", "json"], default="markdown")

    finalize_cmd = subparsers.add_parser("finalize", help="Finalize a ready run")
    finalize_cmd.add_argument("run_id")
    finalize_cmd.add_argument("--write-manifest", action="store_true")
    finalize_cmd.add_argument("--base-dir", type=Path, default=orchestrator.DEFAULT_MANIFEST_BASE_DIR)
    finalize_cmd.add_argument("--format", choices=["markdown", "json"], default="markdown")

    args = parser.parse_args()

    with connect(get_settings()) as conn:
        if args.command == "run":
            payload = orchestrator.start_run(
                conn,
                goal=args.goal,
                workspace_id_or_uri=args.workspace,
                risk_level=args.risk_level,
            )
        elif args.command == "status":
            payload = orchestrator.get_status(conn, args.run_id)
        elif args.command == "dispatch":
            payload = orchestrator.dispatch_claim(
                conn,
                run_id=args.run_id,
                agent_name=args.agent,
            )
        elif args.command == "resume":
            payload = orchestrator.resume_run(conn, args.run_id)
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
