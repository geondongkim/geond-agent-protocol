from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from psycopg import Connection

from geond.storage import dashboard as dashboard_store
from geond_orchestrator import orchestrator, orchestrator_control, orchestrator_planner

ACTION_BUNDLE_SCHEMA = "geond.orchestrator_action_bundle.v1"
ACTION_BUNDLE_JSON_NAME = "ACTION_BUNDLE.json"
ACTION_BUNDLE_MARKDOWN_NAME = "ACTION_BUNDLE.md"


def build_action_bundle(
    conn: Connection,
    *,
    workspace_id_or_uri: str,
    run_id: str | None = None,
    agents: list[str] | None = None,
    limit: int = 50,
    base_dir: Path = orchestrator.DEFAULT_MANIFEST_BASE_DIR,
    write_bundle: bool = False,
) -> dict[str, Any]:
    agent_pool = orchestrator_planner.normalize_agents(agents)
    plan = orchestrator_planner.create_plan(
        conn,
        workspace_id_or_uri=workspace_id_or_uri,
        run_id=run_id,
        agents=agent_pool,
        limit=limit,
        base_dir=base_dir,
        write_bundle=False,
    )
    if plan.get("status") != "ok":
        return plan
    control_preview = None
    if run_id:
        control_preview = orchestrator_control.preview_agent_step(
            conn,
            run_id,
            agents=agent_pool,
            base_dir=base_dir,
            limit=limit,
        )
    trace_refs = trace_refs_by_run(plan, base_dir)
    actions = normalize_action_items(
        plan.get("recommended_actions") or [],
        trace_refs=trace_refs,
    )
    payload = {
        "schema": ACTION_BUNDLE_SCHEMA,
        "status": "ok",
        "code": None,
        "workspace_id_or_uri": workspace_id_or_uri,
        "run_id": run_id,
        "agents": agent_pool,
        "plan_id": plan.get("plan_id"),
        "control_id": (control_preview or {}).get("control_id"),
        "readiness": readiness_summary(plan),
        "actions": actions,
        "action_count": len(actions),
        "blocking_count": sum(1 for action in actions if action.get("blocks_execution")),
        "artifact_refs": sorted(
            {ref for refs in trace_refs.values() for ref in refs},
        ),
        "control_preview": compact_control_preview(control_preview),
    }
    payload["bundle_id"] = stable_bundle_id(payload)
    payload["markdown"] = format_action_bundle_markdown(payload)
    if write_bundle:
        payload["bundle"] = write_action_bundle(payload, base_dir=base_dir)
    return payload


def normalize_action_items(
    actions: list[dict[str, Any]],
    *,
    trace_refs: dict[str, list[str]],
) -> list[dict[str, Any]]:
    normalized = []
    for action in actions:
        run_id = str(action.get("run_id") or "")
        item = {
            "action_id": action_id(action),
            "label": label_for_action(action),
            "action_type": action.get("action_type"),
            "severity": action.get("severity") or "info",
            "status": "blocked" if action.get("blocks_execution") else "ready",
            "reason": action.get("reason"),
            "blocks_execution": bool(action.get("blocks_execution")),
            "suggested_cli_command": action.get("suggested_cli_command"),
            "related_ids": action.get("related_ids") or {},
            "run_id": action.get("run_id"),
            "task_id": action.get("task_id"),
            "artifact_refs": trace_refs.get(run_id, []),
            "task_graph_proposal": action.get("task_graph_proposal"),
            "task_graph_review": action.get("task_graph_review"),
        }
        normalized.append(item)
    return normalized


def trace_refs_by_run(plan: dict[str, Any], base_dir: Path) -> dict[str, list[str]]:
    refs: dict[str, list[str]] = {}
    for run in plan.get("active_runs") or []:
        run_id = str(run.get("run_id") or "")
        if not run_id:
            continue
        artifacts = dashboard_store.read_run_trace_artifacts(run_id, base_dir)
        refs[run_id] = artifact_paths(artifacts)
    return refs


def artifact_paths(artifacts: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for section in (
        artifacts.get("latest_control_bundle"),
        artifacts.get("latest_control_trace"),
        artifacts.get("latest_planner_invocation"),
    ):
        if not isinstance(section, dict):
            continue
        for value in (section.get("artifact_paths") or {}).values():
            if value:
                paths.append(str(value))
    return sorted(set(paths))


def readiness_summary(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "run_id": run.get("run_id"),
            "title": run.get("title"),
            "readiness_status": run.get("readiness_status"),
            "manifest_dir": run.get("manifest_dir"),
        }
        for run in plan.get("active_runs") or []
    ]


def compact_control_preview(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not payload:
        return None
    return {
        "schema": payload.get("schema"),
        "status": payload.get("status"),
        "code": payload.get("code"),
        "control_id": payload.get("control_id"),
        "next_action": payload.get("next_action"),
        "delegated_command": payload.get("delegated_command"),
        "execution_status": payload.get("execution_status"),
    }


def label_for_action(action: dict[str, Any]) -> str:
    action_type = str(action.get("action_type") or "action")
    return action_type.replace("_", " ").title()


def action_id(action: dict[str, Any]) -> str:
    raw = json.dumps(
        {
            "action_type": action.get("action_type"),
            "run_id": action.get("run_id"),
            "task_id": action.get("task_id"),
            "command": action.get("suggested_cli_command"),
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def stable_bundle_id(payload: dict[str, Any]) -> str:
    stable = {
        key: value
        for key, value in payload.items()
        if key not in {"bundle_id", "markdown", "bundle"}
    }
    raw = json.dumps(stable, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def write_action_bundle(payload: dict[str, Any], *, base_dir: Path) -> dict[str, str]:
    target = payload.get("run_id") or workspace_bundle_dir(str(payload.get("workspace_id_or_uri")))
    bundle_dir = base_dir / str(target) / "actions" / str(payload["bundle_id"])
    bundle_dir.mkdir(parents=True, exist_ok=True)
    json_path = bundle_dir / ACTION_BUNDLE_JSON_NAME
    markdown_path = bundle_dir / ACTION_BUNDLE_MARKDOWN_NAME
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(payload.get("markdown", ""), encoding="utf-8")
    return {
        "action_bundle_dir": str(bundle_dir),
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
    }


def workspace_bundle_dir(workspace_id_or_uri: str) -> str:
    digest = hashlib.sha256(workspace_id_or_uri.encode("utf-8")).hexdigest()[:12]
    return f"workspace-{digest}"


def format_action_bundle_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Orchestrator Action Bundle",
        "",
        f"- Bundle: `{payload.get('bundle_id')}`",
        f"- Plan: `{payload.get('plan_id')}`",
        f"- Actions: `{payload.get('action_count')}`",
        f"- Blocking: `{payload.get('blocking_count')}`",
        "",
        "## Actions",
    ]
    actions = payload.get("actions") or []
    if not actions:
        lines.append("- none")
    for action in actions:
        lines.append(
            f"- {action.get('label')} [{action.get('severity')}] "
            f"{action.get('status')}: {action.get('reason')} "
            f"`{action.get('suggested_cli_command')}`"
        )
    return "\n".join(lines).rstrip() + "\n"
