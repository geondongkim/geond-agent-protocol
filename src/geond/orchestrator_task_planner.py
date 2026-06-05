from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from psycopg import Connection

from geond import orchestrator
from geond.storage import orchestration as orchestration_store
from geond.task_graph import normalize_task_graph_payload, parse_task_graph_file

PROPOSAL_SCHEMA = "geond.task_graph_proposal.v1"
MATERIALIZATION_SCHEMA = "geond.task_graph_materialization.v1"
PLANNING_PLACEHOLDER_TITLE = "Plan and dispatch claim-mode workers"
TEMPLATES = {"auto", "bugfix", "implementation", "docs", "ops"}


def propose_task_graph(
    conn: Connection,
    run_id: str,
    *,
    planner: str = "template",
    template: str = "auto",
    agent_name: str = "codex",
    execute_planner: bool = False,
    base_dir: Path = orchestrator.DEFAULT_MANIFEST_BASE_DIR,
    model: str | None = None,
    sandbox: str = "workspace-write",
    timeout_seconds: int = 3600,
    llm_runner: Any | None = None,
    status_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    planner = (planner or "template").strip().lower()
    if planner == "llm":
        from geond import orchestrator_llm_planner

        return orchestrator_llm_planner.propose_task_graph_with_llm(
            conn,
            run_id,
            agent_name=agent_name,
            execute_planner=execute_planner,
            status_payload=status_payload,
            base_dir=base_dir,
            model=model,
            sandbox=sandbox,
            timeout_seconds=timeout_seconds,
            runner=llm_runner,
        )
    if planner != "template":
        return error_result("UNSUPPORTED_PLANNER", f"Unsupported task graph planner: {planner}")
    status_payload = status_payload or orchestrator.get_status(conn, run_id)
    if status_payload.get("status") != "ok":
        return status_payload
    run = status_payload.get("run") or {}
    selected_template = select_template(template, str(run.get("title") or ""))
    if selected_template not in TEMPLATES - {"auto"}:
        return error_result("UNSUPPORTED_TEMPLATE", f"Unsupported task graph template: {template}")
    tasks = template_tasks(selected_template, run)
    validation = validate_task_graph_tasks(tasks)
    if validation.get("status") != "ok":
        return validation
    eligibility = materialization_eligibility(conn, run_id, status_payload=status_payload)
    payload = {
        "schema": PROPOSAL_SCHEMA,
        "status": "ok",
        "code": None,
        "run_id": run_id,
        "planner": "template",
        "planner_agent": None,
        "template": selected_template,
        "requested_template": template,
        "eligible_for_materialization": eligibility["eligible"],
        "eligibility_reason": eligibility["reason"],
        "planning_placeholder_task": eligibility.get("planning_placeholder_task"),
        "tasks": tasks,
    }
    payload["proposal_id"] = stable_proposal_id(payload)
    payload["suggested_apply_command"] = (
        f"geond-orchestrator agent {run_id} --execute "
        f"--allow-task-graph-create --template {selected_template}"
    )
    payload["markdown"] = format_proposal_markdown(payload)
    return payload


def select_template(template: str, text: str) -> str:
    requested = (template or "auto").strip().lower()
    if requested != "auto":
        return requested
    normalized = text.lower()
    if any(token in normalized for token in BUGFIX_KEYWORDS):
        return "bugfix"
    if any(token in normalized for token in DOCS_KEYWORDS):
        return "docs"
    if any(token in normalized for token in OPS_KEYWORDS):
        return "ops"
    return "implementation"


BUGFIX_KEYWORDS = {
    "bug",
    "fix",
    "fail",
    "failing",
    "failed",
    "error",
    "regression",
    "crash",
    "문제",
    "오류",
    "실패",
    "버그",
    "수정",
    "해결",
    "고쳐",
}
DOCS_KEYWORDS = {
    "doc",
    "docs",
    "readme",
    "markdown",
    "guide",
    "문서",
    "계획",
    "작성",
    "정리",
}
OPS_KEYWORDS = {
    "ci",
    "workflow",
    "deploy",
    "release",
    "monitor",
    "incident",
    "ops",
    "운영",
    "배포",
    "릴리스",
    "장애",
}


def template_tasks(template: str, run: dict[str, Any]) -> list[dict[str, Any]]:
    title = str(run.get("title") or "orchestrator run")
    if template == "bugfix":
        return [
            task("repro", "Reproduce and isolate the failure", title, priority=300),
            task(
                "fix",
                "Implement the smallest safe fix",
                title,
                priority=250,
                depends_on=["repro"],
            ),
            task(
                "validate",
                "Run targeted and regression validation",
                title,
                priority=200,
                depends_on=["fix"],
            ),
            task(
                "handoff",
                "Summarize evidence and remaining risk",
                title,
                priority=100,
                depends_on=["validate"],
            ),
        ]
    if template == "docs":
        return [
            task("outline", "Confirm document structure and audience", title, priority=300),
            task(
                "draft",
                "Draft the documentation update",
                title,
                priority=250,
                depends_on=["outline"],
            ),
            task(
                "review",
                "Review links, examples, and wording",
                title,
                priority=200,
                depends_on=["draft"],
            ),
        ]
    if template == "ops":
        return [
            task("inspect", "Inspect current operational state", title, priority=300),
            task(
                "change",
                "Apply the minimal operational change",
                title,
                priority=250,
                depends_on=["inspect"],
            ),
            task(
                "verify",
                "Verify health, rollback notes, and evidence",
                title,
                priority=200,
                depends_on=["change"],
            ),
        ]
    return [
        task("design", "Confirm implementation approach and affected surface", title, priority=300),
        task(
            "implement",
            "Implement the requested change",
            title,
            priority=250,
            depends_on=["design"],
        ),
        task(
            "validate",
            "Run focused validation and summarize evidence",
            title,
            priority=200,
            depends_on=["implement"],
        ),
    ]


def task(
    key: str,
    title: str,
    run_title: str,
    *,
    priority: int,
    depends_on: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "description": f"{title} for run: {run_title}",
        "priority": priority,
        "depends_on": depends_on or [],
        "required_evidence": [],
        "status": "ready",
    }


def validate_task_graph_tasks(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    if not tasks:
        return error_result(
            "VALIDATION_ERROR",
            "Task graph proposal must include at least one task.",
        )
    try:
        normalized = normalize_task_graph_payload({"tasks": tasks})
    except (TypeError, ValueError) as exc:
        return error_result("VALIDATION_ERROR", str(exc))
    keys = [item["key"] for item in normalized["tasks"]]
    if len(set(keys)) != len(keys):
        return error_result("VALIDATION_ERROR", "Task graph keys must be unique.")
    known_keys = set(keys)
    for item in normalized["tasks"]:
        for dependency in item.get("depends_on") or []:
            if dependency not in known_keys:
                return error_result(
                    "TASK_GRAPH_DEPENDENCY_NOT_FOUND",
                    "Task graph dependency key was not found.",
                    related_ids={"dependency_key": dependency, "task_key": item["key"]},
                )
    return {"status": "ok", "code": None, "tasks": normalized["tasks"]}


def materialization_eligibility(
    conn: Connection,
    run_id: str,
    *,
    status_payload: dict[str, Any],
) -> dict[str, Any]:
    graph = orchestration_store.list_task_graph(conn, run_id)
    if graph.get("status") != "ok":
        return {
            "eligible": False,
            "reason": graph.get("message") or "Task graph state could not be read.",
        }
    graph_tasks = [
        item
        for item in graph.get("tasks") or []
        if (item.get("metadata") or {}).get("source") == "task_graph"
    ]
    if graph_tasks or graph.get("edges"):
        return {"eligible": False, "reason": "Run already has a materialized task graph."}
    all_tasks = graph.get("tasks") or []
    placeholder = find_planning_placeholder(all_tasks) or find_planning_placeholder(
        status_payload.get("claimable_tasks") or []
    )
    non_placeholder_tasks = [
        item
        for item in all_tasks
        if not is_planning_placeholder(item) and item.get("status") not in {"cancelled"}
    ]
    if non_placeholder_tasks:
        return {"eligible": False, "reason": "Run already has non-placeholder tasks."}
    if not placeholder:
        return {"eligible": False, "reason": "Run does not have a ready planning placeholder task."}
    return {
        "eligible": True,
        "reason": "Run only has the default planning placeholder task.",
        "planning_placeholder_task": placeholder,
    }


def find_planning_placeholder(tasks: list[dict[str, Any]]) -> dict[str, Any] | None:
    for item in tasks:
        if is_planning_placeholder(item):
            return item
    return None


def is_planning_placeholder(task_item: dict[str, Any]) -> bool:
    return (
        str(task_item.get("title") or "") == PLANNING_PLACEHOLDER_TITLE
        and (task_item.get("metadata") or {}).get("source") != "task_graph"
        and task_item.get("status") in {"ready", "planned", "todo", None}
    )


def apply_task_graph_file(
    conn: Connection,
    run_id: str,
    source_path: Path,
    *,
    execute: bool = False,
) -> dict[str, Any]:
    graph_payload = parse_task_graph_file(source_path)
    return apply_task_graph_payload(
        conn,
        run_id,
        graph_payload,
        execute=execute,
        source_path=source_path,
    )


def apply_task_graph_payload(
    conn: Connection,
    run_id: str,
    graph_payload: dict[str, Any],
    *,
    execute: bool = False,
    source_path: Path | None = None,
) -> dict[str, Any]:
    validation = validate_task_graph_tasks(graph_payload.get("tasks") or [])
    if validation.get("status") != "ok":
        return validation
    status_payload = orchestrator.get_status(conn, run_id)
    if status_payload.get("status") != "ok":
        return status_payload
    eligibility = materialization_eligibility(conn, run_id, status_payload=status_payload)
    base_payload = {
        "schema": MATERIALIZATION_SCHEMA,
        "status": "preview",
        "code": None,
        "run_id": run_id,
        "execute": execute,
        "source_path": str(source_path) if source_path else None,
        "eligible_for_materialization": eligibility["eligible"],
        "eligibility_reason": eligibility["reason"],
        "tasks": validation["tasks"],
        "task_graph_result": None,
        "placeholder_update": None,
    }
    if not execute:
        base_payload["markdown"] = format_materialization_markdown(base_payload)
        return base_payload
    if not eligibility["eligible"]:
        base_payload.update({"status": "skipped", "code": "TASK_GRAPH_NOT_NEEDED"})
        base_payload["markdown"] = format_materialization_markdown(base_payload)
        return base_payload
    graph_result = orchestration_store.create_task_graph(conn, run_id, validation["tasks"])
    base_payload["task_graph_result"] = graph_result
    if graph_result.get("status") != "ok":
        base_payload.update({"status": "error", "code": graph_result.get("code")})
        base_payload["markdown"] = format_materialization_markdown(base_payload)
        return base_payload
    placeholder = eligibility.get("planning_placeholder_task") or {}
    placeholder_update = None
    if placeholder.get("task_id"):
        placeholder_update = orchestration_store.update_task_state(
            conn,
            placeholder["task_id"],
            "done",
            metadata={"source": "task_graph_planner", "materialized_graph": True},
            idempotency_key=f"task_graph:{run_id}:placeholder-done",
        )
    base_payload.update(
        {
            "status": "ok",
            "code": None,
            "placeholder_update": placeholder_update,
        }
    )
    base_payload["markdown"] = format_materialization_markdown(base_payload)
    return base_payload


def write_proposal(payload: dict[str, Any], output_path: Path) -> dict[str, str]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    return {"proposal_path": str(output_path)}


def stable_proposal_id(payload: dict[str, Any]) -> str:
    stable = {
        key: value
        for key, value in payload.items()
        if key not in {"proposal_id", "markdown", "suggested_apply_command"}
    }
    raw = json.dumps(stable, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def format_proposal_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Task Graph Proposal",
        "",
        f"- Proposal: `{payload.get('proposal_id')}`",
        f"- Run: `{payload.get('run_id')}`",
        f"- Template: `{payload.get('template')}`",
        f"- Eligible: `{payload.get('eligible_for_materialization')}`",
        f"- Reason: {payload.get('eligibility_reason')}",
        "",
        "## Tasks",
    ]
    lines.extend(
        markdown_list(
            (
                f"{item.get('key')} | {item.get('title')} | "
                f"priority={item.get('priority')} | "
                f"depends_on={','.join(item.get('depends_on') or [])}"
            )
            for item in payload.get("tasks") or []
        )
    )
    lines.extend(["", "## Apply", f"- `{payload.get('suggested_apply_command')}`"])
    return "\n".join(lines).rstrip() + "\n"


def format_materialization_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Task Graph Materialization",
        "",
        f"- Run: `{payload.get('run_id')}`",
        f"- Status: `{payload.get('status')}`",
        f"- Execute: `{payload.get('execute')}`",
        f"- Eligible: `{payload.get('eligible_for_materialization')}`",
        f"- Reason: {payload.get('eligibility_reason')}",
        "",
        "## Tasks",
    ]
    lines.extend(
        markdown_list(
            f"{item.get('key')} | {item.get('title')}" for item in payload.get("tasks") or []
        )
    )
    return "\n".join(lines).rstrip() + "\n"


def markdown_list(items: Any) -> list[str]:
    values = list(items)
    return [f"- {value}" for value in values] if values else ["- none"]


def error_result(
    code: str,
    message: str,
    *,
    related_ids: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema": PROPOSAL_SCHEMA,
        "status": "error",
        "code": code,
        "message": message,
        "related_ids": related_ids or {},
    }
