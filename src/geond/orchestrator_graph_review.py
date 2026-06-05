from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from psycopg import Connection

from geond import orchestrator, orchestrator_task_planner

REVIEW_SCHEMA = "geond.task_graph_review.v1"
REVIEW_JSON_NAME = "TASK_GRAPH_REVIEW.json"
REVIEW_MARKDOWN_NAME = "TASK_GRAPH_REVIEW.md"


def review_task_graph_proposal(
    conn: Connection,
    run_id: str,
    proposal_payload: dict[str, Any],
    *,
    base_dir: Path = orchestrator.DEFAULT_MANIFEST_BASE_DIR,
    source_path: Path | None = None,
    write_bundle: bool = False,
) -> dict[str, Any]:
    proposal = extract_proposal_payload(proposal_payload)
    if proposal is None:
        return error_result("TASK_GRAPH_PROPOSAL_MISSING", "No task graph proposal was found.")

    findings: list[dict[str, Any]] = []
    validation = orchestrator_task_planner.validate_task_graph_tasks(proposal.get("tasks") or [])
    if validation.get("status") != "ok":
        findings.append(
            finding(
                "error",
                str(validation.get("code") or "VALIDATION_ERROR"),
                str(validation.get("message") or "Task graph proposal failed validation."),
                validation.get("related_ids") or {},
            )
        )
        tasks: list[dict[str, Any]] = []
    else:
        tasks = validation["tasks"]
        cycle = find_dependency_cycle(tasks)
        if cycle:
            findings.append(
                finding(
                    "error",
                    "TASK_GRAPH_CYCLE",
                    "Task graph dependencies contain a cycle.",
                    {"cycle": cycle},
                )
            )
        findings.extend(required_evidence_findings(tasks))

    status_payload = orchestrator.get_status(conn, run_id)
    if status_payload.get("status") != "ok":
        findings.append(
            finding(
                "error",
                str(status_payload.get("code") or "RUN_NOT_FOUND"),
                str(status_payload.get("message") or "Run status could not be read."),
            )
        )
        eligibility = {"eligible": False, "reason": "Run status could not be read."}
    else:
        eligibility = orchestrator_task_planner.materialization_eligibility(
            conn,
            run_id,
            status_payload=status_payload,
        )
        if not eligibility.get("eligible"):
            findings.append(
                finding(
                    "error",
                    "TASK_GRAPH_NOT_ELIGIBLE",
                    str(eligibility.get("reason") or "Task graph cannot be materialized."),
                )
            )

    decision = review_decision(findings)
    payload = {
        "schema": REVIEW_SCHEMA,
        "status": "ok",
        "code": None,
        "run_id": run_id,
        "proposal_id": proposal.get("proposal_id"),
        "planner": proposal.get("planner") or "template",
        "planner_agent": proposal.get("planner_agent"),
        "decision": decision,
        "review_score": review_score(findings),
        "findings": findings,
        "task_count": len(tasks),
        "eligible_for_materialization": bool(eligibility.get("eligible")),
        "eligibility_reason": eligibility.get("reason"),
        "source_path": str(source_path) if source_path else None,
        "suggested_next_command": suggested_next_command(run_id, decision, proposal, source_path),
    }
    payload["review_id"] = stable_review_id(payload)
    payload["markdown"] = format_review_markdown(payload)
    if write_bundle:
        payload["bundle"] = write_review_bundle(payload, base_dir=base_dir)
    return payload


def review_task_graph_file(
    conn: Connection,
    run_id: str,
    source_path: Path,
    *,
    base_dir: Path = orchestrator.DEFAULT_MANIFEST_BASE_DIR,
    write_bundle: bool = False,
) -> dict[str, Any]:
    try:
        payload = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return error_result("TASK_GRAPH_PROPOSAL_INVALID_JSON", str(exc))
    if not isinstance(payload, dict):
        return error_result("TASK_GRAPH_PROPOSAL_INVALID", "Proposal file must contain an object.")
    return review_task_graph_proposal(
        conn,
        run_id,
        payload,
        base_dir=base_dir,
        source_path=source_path,
        write_bundle=write_bundle,
    )


def review_latest_planner_result(
    conn: Connection,
    run_id: str,
    *,
    base_dir: Path = orchestrator.DEFAULT_MANIFEST_BASE_DIR,
    write_bundle: bool = False,
) -> dict[str, Any]:
    planner_dir = latest_child_dir(base_dir / run_id / "planner")
    if planner_dir is None:
        return error_result("PLANNER_RESULT_NOT_FOUND", "No planner invocation artifact was found.")
    result_path = planner_dir / "RESULT.json"
    result = read_json_object(result_path)
    if result is None:
        return error_result(
            "PLANNER_RESULT_NOT_FOUND",
            "Latest planner invocation did not include RESULT.json.",
        )
    return review_task_graph_proposal(
        conn,
        run_id,
        result,
        base_dir=base_dir,
        source_path=result_path,
        write_bundle=write_bundle,
    )


def extract_proposal_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    if payload.get("schema") == orchestrator_task_planner.PROPOSAL_SCHEMA:
        return payload
    proposal = payload.get("task_graph_proposal") or payload.get("proposal")
    return proposal if isinstance(proposal, dict) else None


def required_evidence_findings(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings = []
    for task in tasks:
        if task.get("required_evidence"):
            continue
        findings.append(
            finding(
                "warning",
                "REQUIRED_EVIDENCE_MISSING",
                "Task does not declare required evidence.",
                {"task_key": task.get("key")},
            )
        )
    return findings


def find_dependency_cycle(tasks: list[dict[str, Any]]) -> list[str]:
    graph = {str(task["key"]): [str(dep) for dep in task.get("depends_on") or []] for task in tasks}
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def visit(key: str) -> list[str]:
        if key in visiting:
            start = stack.index(key) if key in stack else 0
            return stack[start:] + [key]
        if key in visited:
            return []
        visiting.add(key)
        stack.append(key)
        for dependency in graph.get(key, []):
            cycle = visit(dependency)
            if cycle:
                return cycle
        stack.pop()
        visiting.remove(key)
        visited.add(key)
        return []

    for key in graph:
        cycle = visit(key)
        if cycle:
            return cycle
    return []


def review_decision(findings: list[dict[str, Any]]) -> str:
    if any(item.get("severity") == "error" for item in findings):
        return "blocked"
    if any(item.get("severity") == "warning" for item in findings):
        return "needs_revision"
    return "approved"


def review_score(findings: list[dict[str, Any]]) -> int:
    penalty = 0
    for item in findings:
        penalty += 40 if item.get("severity") == "error" else 10
    return max(0, 100 - penalty)


def finding(
    severity: str,
    code: str,
    message: str,
    related_ids: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "related_ids": related_ids or {},
    }


def suggested_next_command(
    run_id: str,
    decision: str,
    proposal: dict[str, Any],
    source_path: Path | None,
) -> str:
    if decision == "approved" and source_path:
        return f"geond-orchestrator graph apply {run_id} --from {source_path} --execute"
    if decision == "approved":
        planner = str(proposal.get("planner") or "template")
        if planner == "llm":
            return (
                f"geond-orchestrator agent {run_id} --execute --planner llm "
                "--allow-llm-planner --execute-planner --allow-task-graph-create"
            )
        return f"geond-orchestrator agent {run_id} --execute --allow-task-graph-create"
    return f"geond-orchestrator graph propose {run_id} --planner llm --execute-planner"


def stable_review_id(payload: dict[str, Any]) -> str:
    stable = {
        key: value
        for key, value in payload.items()
        if key not in {"review_id", "markdown", "bundle"}
    }
    raw = json.dumps(stable, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def write_review_bundle(payload: dict[str, Any], *, base_dir: Path) -> dict[str, str]:
    review_dir = base_dir / str(payload.get("run_id")) / "reviews" / str(payload["review_id"])
    review_dir.mkdir(parents=True, exist_ok=True)
    json_path = review_dir / REVIEW_JSON_NAME
    markdown_path = review_dir / REVIEW_MARKDOWN_NAME
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(payload.get("markdown", ""), encoding="utf-8")
    return {
        "review_dir": str(review_dir),
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
    }


def format_review_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Task Graph Review",
        "",
        f"- Review: `{payload.get('review_id')}`",
        f"- Run: `{payload.get('run_id')}`",
        f"- Proposal: `{payload.get('proposal_id')}`",
        f"- Decision: `{payload.get('decision')}`",
        f"- Score: `{payload.get('review_score')}`",
        "",
        "## Findings",
    ]
    findings = payload.get("findings") or []
    if findings:
        for item in findings:
            lines.append(
                f"- {item.get('severity')} {item.get('code')}: {item.get('message')}"
            )
    else:
        lines.append("- none")
    lines.extend(["", "## Next Command", f"- `{payload.get('suggested_next_command')}`"])
    return "\n".join(lines).rstrip() + "\n"


def latest_child_dir(parent: Path) -> Path | None:
    if not parent.exists() or not parent.is_dir():
        return None
    children = [item for item in parent.iterdir() if item.is_dir()]
    if not children:
        return None
    return max(children, key=lambda item: item.stat().st_mtime)


def read_json_object(path: Path) -> dict[str, Any] | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def error_result(code: str, message: str) -> dict[str, Any]:
    payload = {
        "schema": REVIEW_SCHEMA,
        "status": "error",
        "code": code,
        "message": message,
        "decision": "blocked",
        "findings": [finding("error", code, message)],
    }
    payload["review_id"] = stable_review_id(payload)
    payload["markdown"] = format_review_markdown(payload)
    return payload
