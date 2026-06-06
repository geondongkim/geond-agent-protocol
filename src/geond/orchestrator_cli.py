from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from geond import (
    orchestrator,
    orchestrator_action_bundle,
    orchestrator_action_queue,
    orchestrator_budget,
    orchestrator_control,
    orchestrator_daemon,
    orchestrator_graph_review,
    orchestrator_planner,
    orchestrator_scheduler,
    orchestrator_task_planner,
)
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

    plan_cmd = subparsers.add_parser("plan", help="Recommend safe next orchestrator actions")
    plan_cmd.add_argument("--workspace", required=True)
    plan_cmd.add_argument("--run", dest="run_id")
    plan_cmd.add_argument("--agents")
    plan_cmd.add_argument("--propose-task-graph", action="store_true")
    plan_cmd.add_argument("--planner", choices=["template", "llm"], default="template")
    plan_cmd.add_argument("--template", default="auto")
    plan_cmd.add_argument("--planner-agent", default="codex")
    plan_cmd.add_argument("--write-bundle", action="store_true")
    plan_cmd.add_argument(
        "--base-dir",
        type=Path,
        default=orchestrator.DEFAULT_MANIFEST_BASE_DIR,
    )
    plan_cmd.add_argument("--limit", type=int, default=50)
    plan_cmd.add_argument("--format", choices=["markdown", "json"], default="markdown")

    actions_cmd = subparsers.add_parser("actions", help="Build an operator action bundle")
    actions_cmd.add_argument("--workspace", required=True)
    actions_cmd.add_argument("--run", dest="run_id")
    actions_cmd.add_argument("--agents")
    actions_cmd.add_argument("--write-bundle", action="store_true")
    actions_cmd.add_argument(
        "--base-dir",
        type=Path,
        default=orchestrator.DEFAULT_MANIFEST_BASE_DIR,
    )
    actions_cmd.add_argument("--limit", type=int, default=50)
    actions_cmd.add_argument("--format", choices=["markdown", "json"], default="markdown")

    action_cmd = subparsers.add_parser(
        "action",
        help="Queue, approve, and execute operator actions",
    )
    action_subparsers = action_cmd.add_subparsers(dest="action_command", required=True)
    action_queue = action_subparsers.add_parser("queue", help="Queue current operator actions")
    action_queue.add_argument("--workspace", required=True)
    action_queue.add_argument("--run", dest="run_id")
    action_queue.add_argument("--agents")
    action_queue.add_argument("--write-bundle", action="store_true")
    action_queue.add_argument(
        "--base-dir",
        type=Path,
        default=orchestrator.DEFAULT_MANIFEST_BASE_DIR,
    )
    action_queue.add_argument("--limit", type=int, default=50)
    action_queue.add_argument("--format", choices=["markdown", "json"], default="markdown")
    action_list = action_subparsers.add_parser("list", help="List queued operator actions")
    action_list.add_argument("--workspace", required=True)
    action_list.add_argument("--run", dest="run_id")
    action_list.add_argument("--agents")
    action_list.add_argument(
        "--base-dir",
        type=Path,
        default=orchestrator.DEFAULT_MANIFEST_BASE_DIR,
    )
    action_list.add_argument("--limit", type=int, default=50)
    action_list.add_argument("--format", choices=["markdown", "json"], default="markdown")
    action_approve = action_subparsers.add_parser("approve", help="Approve a queued action")
    action_approve.add_argument("run_id")
    action_approve.add_argument("action_id")
    action_approve.add_argument("--approved-by", required=True)
    action_approve.add_argument("--reason", default="")
    action_approve.add_argument(
        "--base-dir",
        type=Path,
        default=orchestrator.DEFAULT_MANIFEST_BASE_DIR,
    )
    action_approve.add_argument("--format", choices=["markdown", "json"], default="markdown")
    action_reject = action_subparsers.add_parser("reject", help="Reject a queued action")
    action_reject.add_argument("run_id")
    action_reject.add_argument("action_id")
    action_reject.add_argument("--rejected-by", required=True)
    action_reject.add_argument("--reason", required=True)
    action_reject.add_argument(
        "--base-dir",
        type=Path,
        default=orchestrator.DEFAULT_MANIFEST_BASE_DIR,
    )
    action_reject.add_argument("--format", choices=["markdown", "json"], default="markdown")
    action_execute = action_subparsers.add_parser("execute", help="Preview or execute one action")
    action_execute.add_argument("run_id")
    action_execute.add_argument("action_id")
    action_execute.add_argument("--execute", action="store_true")
    action_execute.add_argument("--agents")
    action_execute.add_argument("--max-workers", type=int, default=1)
    action_execute.add_argument("--model")
    action_execute.add_argument("--sandbox", default="workspace-write")
    action_execute.add_argument("--timeout-seconds", type=int, default=3600)
    action_execute.add_argument(
        "--base-dir",
        type=Path,
        default=orchestrator.DEFAULT_MANIFEST_BASE_DIR,
    )
    action_execute.add_argument("--format", choices=["markdown", "json"], default="markdown")

    scheduler_cmd = subparsers.add_parser("scheduler", help="Plan or drain queued actions")
    scheduler_subparsers = scheduler_cmd.add_subparsers(dest="scheduler_command", required=True)
    scheduler_plan = scheduler_subparsers.add_parser("plan", help="Preview scheduler selections")
    scheduler_plan.add_argument("--workspace", required=True)
    scheduler_plan.add_argument("--run", dest="run_id")
    scheduler_plan.add_argument("--agents")
    scheduler_plan.add_argument("--max-actions", type=int, default=5)
    scheduler_plan.add_argument("--max-workers", type=int, default=1)
    scheduler_plan.add_argument("--model")
    scheduler_plan.add_argument("--sandbox", default="workspace-write")
    scheduler_plan.add_argument("--timeout-seconds", type=int, default=3600)
    scheduler_plan.add_argument("--budget-actions", type=int)
    scheduler_plan.add_argument("--budget-spawn-actions", type=int)
    scheduler_plan.add_argument("--budget-tokens", type=int)
    scheduler_plan.add_argument("--budget-cost-usd")
    scheduler_plan.add_argument("--budget-window-hours", type=int, default=24)
    scheduler_plan.add_argument("--estimate-spawn-tokens", type=int, default=0)
    scheduler_plan.add_argument("--estimate-spawn-cost-usd")
    scheduler_plan.add_argument("--write-bundle", action="store_true")
    scheduler_plan.add_argument(
        "--base-dir",
        type=Path,
        default=orchestrator.DEFAULT_MANIFEST_BASE_DIR,
    )
    scheduler_plan.add_argument("--limit", type=int, default=50)
    scheduler_plan.add_argument("--format", choices=["markdown", "json"], default="markdown")
    scheduler_drain = scheduler_subparsers.add_parser(
        "drain",
        help="Preview or execute approved queued actions",
    )
    scheduler_drain.add_argument("--workspace", required=True)
    scheduler_drain.add_argument("--run", dest="run_id")
    scheduler_drain.add_argument("--execute", action="store_true")
    scheduler_drain.add_argument("--agents")
    scheduler_drain.add_argument("--max-actions", type=int, default=5)
    scheduler_drain.add_argument("--max-workers", type=int, default=1)
    scheduler_drain.add_argument("--model")
    scheduler_drain.add_argument("--sandbox", default="workspace-write")
    scheduler_drain.add_argument("--timeout-seconds", type=int, default=3600)
    scheduler_drain.add_argument("--budget-actions", type=int)
    scheduler_drain.add_argument("--budget-spawn-actions", type=int)
    scheduler_drain.add_argument("--budget-tokens", type=int)
    scheduler_drain.add_argument("--budget-cost-usd")
    scheduler_drain.add_argument("--budget-window-hours", type=int, default=24)
    scheduler_drain.add_argument("--estimate-spawn-tokens", type=int, default=0)
    scheduler_drain.add_argument("--estimate-spawn-cost-usd")
    scheduler_drain.add_argument("--write-bundle", action="store_true")
    scheduler_drain.add_argument(
        "--base-dir",
        type=Path,
        default=orchestrator.DEFAULT_MANIFEST_BASE_DIR,
    )
    scheduler_drain.add_argument("--limit", type=int, default=50)
    scheduler_drain.add_argument("--format", choices=["markdown", "json"], default="markdown")

    budget_cmd = subparsers.add_parser("budget", help="Inspect orchestrator budget state")
    budget_subparsers = budget_cmd.add_subparsers(dest="budget_command", required=True)
    budget_report = budget_subparsers.add_parser("report", help="Report scheduler budget state")
    budget_report.add_argument("--workspace", required=True)
    budget_report.add_argument("--run", dest="run_id")
    budget_report.add_argument("--agents")
    budget_report.add_argument("--max-actions", type=int, default=5)
    budget_report.add_argument("--budget-actions", type=int)
    budget_report.add_argument("--budget-spawn-actions", type=int)
    budget_report.add_argument("--budget-tokens", type=int)
    budget_report.add_argument("--budget-cost-usd")
    budget_report.add_argument("--budget-window-hours", type=int, default=24)
    budget_report.add_argument("--estimate-spawn-tokens", type=int, default=0)
    budget_report.add_argument("--estimate-spawn-cost-usd")
    budget_report.add_argument(
        "--base-dir",
        type=Path,
        default=orchestrator.DEFAULT_MANIFEST_BASE_DIR,
    )
    budget_report.add_argument("--limit", type=int, default=50)
    budget_report.add_argument("--format", choices=["markdown", "json"], default="markdown")

    daemon_cmd = subparsers.add_parser("daemon", help="Run local scheduler daemon cycles")
    daemon_subparsers = daemon_cmd.add_subparsers(dest="daemon_command", required=True)
    daemon_once = daemon_subparsers.add_parser("once", help="Preview or run one daemon cycle")
    daemon_run = daemon_subparsers.add_parser("run", help="Run repeated daemon cycles")
    for daemon_parser in (daemon_once, daemon_run):
        daemon_parser.add_argument("--workspace", required=True)
        daemon_parser.add_argument("--run", dest="run_id")
        daemon_parser.add_argument("--execute", action="store_true")
        daemon_parser.add_argument("--agents")
        daemon_parser.add_argument("--max-actions", type=int, default=5)
        daemon_parser.add_argument("--max-workers", type=int, default=1)
        daemon_parser.add_argument("--model")
        daemon_parser.add_argument("--sandbox", default="workspace-write")
        daemon_parser.add_argument("--timeout-seconds", type=int, default=3600)
        daemon_parser.add_argument("--budget-actions", type=int)
        daemon_parser.add_argument("--budget-spawn-actions", type=int)
        daemon_parser.add_argument("--budget-tokens", type=int)
        daemon_parser.add_argument("--budget-cost-usd")
        daemon_parser.add_argument("--budget-window-hours", type=int, default=24)
        daemon_parser.add_argument("--estimate-spawn-tokens", type=int, default=0)
        daemon_parser.add_argument("--estimate-spawn-cost-usd")
        daemon_parser.add_argument("--interval-seconds", type=int, default=60)
        daemon_parser.add_argument(
            "--base-dir",
            type=Path,
            default=orchestrator.DEFAULT_MANIFEST_BASE_DIR,
        )
        daemon_parser.add_argument("--limit", type=int, default=50)
        daemon_parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    daemon_run.add_argument("--max-cycles", type=int, default=1)
    daemon_run.add_argument("--forever", action="store_true")

    agent_cmd = subparsers.add_parser("agent", help="Preview or execute the next safe agent step")
    agent_cmd.add_argument("run_id")
    agent_cmd.add_argument("--execute", action="store_true")
    agent_cmd.add_argument("--allow-task-graph-create", action="store_true")
    agent_cmd.add_argument("--allow-llm-planner", action="store_true")
    agent_cmd.add_argument("--execute-planner", action="store_true")
    agent_cmd.add_argument("--max-steps", type=int, default=1)
    agent_cmd.add_argument("--agents")
    agent_cmd.add_argument("--planner", choices=["template", "llm"], default="template")
    agent_cmd.add_argument("--template", default="auto")
    agent_cmd.add_argument("--planner-agent", default="codex")
    agent_cmd.add_argument("--max-workers", type=int, default=1)
    agent_cmd.add_argument("--model")
    agent_cmd.add_argument("--sandbox", default="workspace-write")
    agent_cmd.add_argument("--timeout-seconds", type=int, default=3600)
    agent_cmd.add_argument("--write-bundle", action="store_true")
    agent_cmd.add_argument(
        "--base-dir",
        type=Path,
        default=orchestrator.DEFAULT_MANIFEST_BASE_DIR,
    )
    agent_cmd.add_argument("--limit", type=int, default=50)
    agent_cmd.add_argument("--format", choices=["markdown", "json"], default="markdown")

    doctor_cmd = subparsers.add_parser("doctor", help="Diagnose one orchestrator run")
    doctor_cmd.add_argument("run_id")
    doctor_cmd.add_argument("--agents")
    doctor_cmd.add_argument(
        "--base-dir",
        type=Path,
        default=orchestrator.DEFAULT_MANIFEST_BASE_DIR,
    )
    doctor_cmd.add_argument("--limit", type=int, default=50)
    doctor_cmd.add_argument("--format", choices=["markdown", "json"], default="markdown")

    dispatch_cmd = subparsers.add_parser("dispatch", help="Dispatch claim-mode or spawn-mode work")
    dispatch_cmd.add_argument("--run", dest="run_id", required=True)
    dispatch_cmd.add_argument("--mode", choices=["claim", "spawn"], default="claim")
    dispatch_cmd.add_argument("--agent", default="codex")
    dispatch_cmd.add_argument("--agents")
    dispatch_cmd.add_argument("--execute", action="store_true")
    dispatch_cmd.add_argument("--task", dest="task_ids", action="append")
    dispatch_cmd.add_argument("--model")
    dispatch_cmd.add_argument("--sandbox", default="workspace-write")
    dispatch_cmd.add_argument("--timeout-seconds", type=int, default=3600)
    dispatch_cmd.add_argument("--write-bundle", action="store_true")
    dispatch_cmd.add_argument("--max-workers", type=int, default=1)
    dispatch_cmd.add_argument(
        "--base-dir",
        type=Path,
        default=orchestrator.DEFAULT_MANIFEST_BASE_DIR,
    )
    dispatch_cmd.add_argument("--format", choices=["markdown", "json"], default="markdown")

    graph_cmd = subparsers.add_parser("graph", help="Propose or apply task graphs")
    graph_subparsers = graph_cmd.add_subparsers(dest="graph_command", required=True)
    graph_propose = graph_subparsers.add_parser("propose", help="Propose a task graph")
    graph_propose.add_argument("run_id")
    graph_propose.add_argument("--planner", choices=["template", "llm"], default="template")
    graph_propose.add_argument("--agent", default="codex")
    graph_propose.add_argument("--execute-planner", action="store_true")
    graph_propose.add_argument("--template", default="auto")
    graph_propose.add_argument(
        "--base-dir",
        type=Path,
        default=orchestrator.DEFAULT_MANIFEST_BASE_DIR,
    )
    graph_propose.add_argument("--model")
    graph_propose.add_argument("--sandbox", default="workspace-write")
    graph_propose.add_argument("--timeout-seconds", type=int, default=3600)
    graph_propose.add_argument("--output", type=Path)
    graph_propose.add_argument("--format", choices=["markdown", "json"], default="markdown")
    graph_apply = graph_subparsers.add_parser("apply", help="Apply a task graph proposal")
    graph_apply.add_argument("run_id")
    graph_apply.add_argument("--from", dest="source_path", type=Path, required=True)
    graph_apply.add_argument("--execute", action="store_true")
    graph_apply.add_argument("--format", choices=["markdown", "json"], default="markdown")
    graph_review = graph_subparsers.add_parser("review", help="Review a task graph proposal")
    graph_review.add_argument("run_id")
    graph_review_source = graph_review.add_mutually_exclusive_group(required=True)
    graph_review_source.add_argument("--from", dest="source_path", type=Path)
    graph_review_source.add_argument("--latest-planner", action="store_true")
    graph_review.add_argument("--write-bundle", action="store_true")
    graph_review.add_argument(
        "--base-dir",
        type=Path,
        default=orchestrator.DEFAULT_MANIFEST_BASE_DIR,
    )
    graph_review.add_argument("--format", choices=["markdown", "json"], default="markdown")

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
    finalize_cmd.add_argument("--git-checkpoint", action="store_true")
    finalize_cmd.add_argument("--commit", action="store_true")
    finalize_cmd.add_argument("--path", dest="paths", action="append")
    finalize_cmd.add_argument("--stage-all", action="store_true")
    finalize_cmd.add_argument("--commit-message")
    finalize_cmd.add_argument("--push", action="store_true")
    finalize_cmd.add_argument("--remote", default="origin")
    finalize_cmd.add_argument("--branch", default="CURRENT")
    finalize_cmd.add_argument("--create-pr", action="store_true")
    finalize_cmd.add_argument("--pr-title")
    finalize_cmd.add_argument("--pr-body-file", type=Path)
    finalize_cmd.add_argument("--dry-run", action="store_true")
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
        elif args.command == "plan":
            payload = orchestrator_control.run_plan_mode(
                conn,
                workspace_id_or_uri=args.workspace,
                run_id=args.run_id,
                agents=parse_agents(args.agents),
                limit=args.limit,
                base_dir=args.base_dir,
                write_bundle=args.write_bundle,
                propose_task_graph=args.propose_task_graph,
                planner=args.planner,
                template=args.template,
                planner_agent=args.planner_agent,
            )
        elif args.command == "actions":
            payload = orchestrator_action_bundle.build_action_bundle(
                conn,
                workspace_id_or_uri=args.workspace,
                run_id=args.run_id,
                agents=parse_agents(args.agents),
                limit=args.limit,
                base_dir=args.base_dir,
                write_bundle=args.write_bundle,
            )
        elif args.command == "action":
            if args.action_command == "queue":
                payload = orchestrator_action_queue.queue_actions_from_bundle(
                    conn,
                    workspace_id_or_uri=args.workspace,
                    run_id=args.run_id,
                    agents=parse_agents(args.agents),
                    limit=args.limit,
                    base_dir=args.base_dir,
                    write_bundle=args.write_bundle,
                )
            elif args.action_command == "list":
                payload = orchestrator_action_queue.list_action_queue(
                    conn,
                    workspace_id_or_uri=args.workspace,
                    run_id=args.run_id,
                    agents=parse_agents(args.agents),
                    limit=args.limit,
                    base_dir=args.base_dir,
                )
            elif args.action_command == "approve":
                payload = orchestrator_action_queue.approve_action(
                    run_id=args.run_id,
                    action_id=args.action_id,
                    approved_by=args.approved_by,
                    reason=args.reason,
                    base_dir=args.base_dir,
                )
            elif args.action_command == "reject":
                payload = orchestrator_action_queue.reject_action(
                    run_id=args.run_id,
                    action_id=args.action_id,
                    rejected_by=args.rejected_by,
                    reason=args.reason,
                    base_dir=args.base_dir,
                )
            else:
                payload = orchestrator_action_queue.execute_queued_action(
                    conn,
                    run_id=args.run_id,
                    action_id=args.action_id,
                    execute=args.execute,
                    agents=parse_agents(args.agents),
                    max_workers=args.max_workers,
                    model=args.model,
                    sandbox=args.sandbox,
                    timeout_seconds=args.timeout_seconds,
                    base_dir=args.base_dir,
                )
        elif args.command == "scheduler":
            if args.scheduler_command == "plan":
                payload = orchestrator_scheduler.plan_scheduler(
                    conn,
                    workspace_id_or_uri=args.workspace,
                    run_id=args.run_id,
                    agents=parse_agents(args.agents),
                    max_actions=args.max_actions,
                    max_workers=args.max_workers,
                    model=args.model,
                    sandbox=args.sandbox,
                    timeout_seconds=args.timeout_seconds,
                    base_dir=args.base_dir,
                    budget_actions=args.budget_actions,
                    budget_spawn_actions=args.budget_spawn_actions,
                    budget_tokens=args.budget_tokens,
                    budget_cost_usd=args.budget_cost_usd,
                    budget_window_hours=args.budget_window_hours,
                    estimate_spawn_tokens=args.estimate_spawn_tokens,
                    estimate_spawn_cost_usd=args.estimate_spawn_cost_usd,
                    limit=args.limit,
                    write_bundle=args.write_bundle,
                )
            else:
                payload = orchestrator_scheduler.drain_scheduler(
                    conn,
                    workspace_id_or_uri=args.workspace,
                    run_id=args.run_id,
                    execute=args.execute,
                    agents=parse_agents(args.agents),
                    max_actions=args.max_actions,
                    max_workers=args.max_workers,
                    model=args.model,
                    sandbox=args.sandbox,
                    timeout_seconds=args.timeout_seconds,
                    base_dir=args.base_dir,
                    budget_actions=args.budget_actions,
                    budget_spawn_actions=args.budget_spawn_actions,
                    budget_tokens=args.budget_tokens,
                    budget_cost_usd=args.budget_cost_usd,
                    budget_window_hours=args.budget_window_hours,
                    estimate_spawn_tokens=args.estimate_spawn_tokens,
                    estimate_spawn_cost_usd=args.estimate_spawn_cost_usd,
                    limit=args.limit,
                    write_bundle=args.write_bundle,
                )
        elif args.command == "budget":
            payload = orchestrator_budget.build_budget_report(
                conn,
                workspace_id_or_uri=args.workspace,
                run_id=args.run_id,
                agents=parse_agents(args.agents),
                max_actions=args.max_actions,
                base_dir=args.base_dir,
                budget_actions=args.budget_actions,
                budget_spawn_actions=args.budget_spawn_actions,
                budget_tokens=args.budget_tokens,
                budget_cost_usd=args.budget_cost_usd,
                budget_window_hours=args.budget_window_hours,
                estimate_spawn_tokens=args.estimate_spawn_tokens,
                estimate_spawn_cost_usd=args.estimate_spawn_cost_usd,
                limit=args.limit,
            )
        elif args.command == "daemon":
            common = {
                "workspace_id_or_uri": args.workspace,
                "run_id": args.run_id,
                "execute": args.execute,
                "agents": parse_agents(args.agents),
                "max_actions": args.max_actions,
                "max_workers": args.max_workers,
                "model": args.model,
                "sandbox": args.sandbox,
                "timeout_seconds": args.timeout_seconds,
                "base_dir": args.base_dir,
                "budget_actions": args.budget_actions,
                "budget_spawn_actions": args.budget_spawn_actions,
                "budget_tokens": args.budget_tokens,
                "budget_cost_usd": args.budget_cost_usd,
                "budget_window_hours": args.budget_window_hours,
                "estimate_spawn_tokens": args.estimate_spawn_tokens,
                "estimate_spawn_cost_usd": args.estimate_spawn_cost_usd,
                "interval_seconds": args.interval_seconds,
                "limit": args.limit,
            }
            if args.daemon_command == "once":
                payload = orchestrator_daemon.run_daemon_once(conn, **common)
            else:
                payload = orchestrator_daemon.run_daemon_loop(
                    conn,
                    max_cycles=args.max_cycles,
                    forever=args.forever,
                    **common,
                )
        elif args.command == "agent":
            payload = orchestrator_control.run_agent_mode(
                conn,
                args.run_id,
                execute=args.execute,
                max_steps=args.max_steps,
                agents=parse_agents(args.agents),
                max_workers=args.max_workers,
                model=args.model,
                sandbox=args.sandbox,
                timeout_seconds=args.timeout_seconds,
                write_bundle=args.write_bundle,
                base_dir=args.base_dir,
                limit=args.limit,
                allow_task_graph_create=args.allow_task_graph_create,
                planner=args.planner,
                template=args.template,
                planner_agent=args.planner_agent,
                allow_llm_planner=args.allow_llm_planner,
                execute_planner=args.execute_planner,
            )
        elif args.command == "doctor":
            payload = orchestrator_planner.doctor_run(
                conn,
                args.run_id,
                agents=parse_agents(args.agents),
                limit=args.limit,
                base_dir=args.base_dir,
            )
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
                    task_id=args.task_ids[0] if args.task_ids else None,
                    task_ids=args.task_ids if args.task_ids and len(args.task_ids) > 1 else None,
                    agent_names=parse_agents(args.agents),
                    max_workers=args.max_workers,
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
        elif args.command == "graph":
            if args.graph_command == "propose":
                payload = orchestrator_task_planner.propose_task_graph(
                    conn,
                    args.run_id,
                    planner=args.planner,
                    template=args.template,
                    agent_name=args.agent,
                    execute_planner=args.execute_planner,
                    base_dir=args.base_dir,
                    model=args.model,
                    sandbox=args.sandbox,
                    timeout_seconds=args.timeout_seconds,
                )
                output_payload = payload.get("task_graph_proposal") or payload
                if args.output and output_payload.get("status") == "ok":
                    payload["output"] = orchestrator_task_planner.write_proposal(
                        output_payload,
                        args.output,
                    )
                    if output_payload is not payload:
                        payload["task_graph_proposal"] = output_payload
            else:
                if args.graph_command == "apply":
                    payload = orchestrator_task_planner.apply_task_graph_file(
                        conn,
                        args.run_id,
                        args.source_path,
                        execute=args.execute,
                    )
                elif args.latest_planner:
                    payload = orchestrator_graph_review.review_latest_planner_result(
                        conn,
                        args.run_id,
                        base_dir=args.base_dir,
                        write_bundle=args.write_bundle,
                    )
                else:
                    payload = orchestrator_graph_review.review_task_graph_file(
                        conn,
                        args.run_id,
                        args.source_path,
                        base_dir=args.base_dir,
                        write_bundle=args.write_bundle,
                    )
        elif args.command == "finalize":
            payload = orchestrator.finalize_run(
                conn,
                args.run_id,
                write_manifest=args.write_manifest,
                manifest_base_dir=args.base_dir,
                git_checkpoint=args.git_checkpoint,
                commit=args.commit,
                paths=args.paths or [],
                stage_all=args.stage_all,
                commit_message=args.commit_message,
                push=args.push,
                remote=args.remote,
                branch=args.branch,
                create_pr=args.create_pr,
                pr_title=args.pr_title,
                pr_body_file=args.pr_body_file,
                dry_run=args.dry_run,
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


def parse_agents(value: str | None) -> list[str] | None:
    if not value:
        return None
    agents = [item.strip() for item in value.split(",") if item.strip()]
    return agents or None


if __name__ == "__main__":
    main()
