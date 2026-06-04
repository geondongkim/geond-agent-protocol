from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MANIFEST_SCHEMA = "geond.run_manifest.v1"


def write_run_manifest(
    package: dict[str, Any],
    summary_markdown: str,
    *,
    base_dir: Path,
    write_result: bool = False,
) -> dict[str, Any]:
    if package.get("status") != "ok":
        return package
    run = package.get("run") or {}
    run_id = run.get("run_id")
    if not run_id:
        return {
            "status": "error",
            "code": "RUN_NOT_FOUND",
            "message": "Run package did not include a run_id.",
            "related_ids": {},
        }

    run_dir = base_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    files: dict[str, str] = {}
    manifest = build_manifest(package)
    files["manifest"] = write_json(run_dir / "EVIDENCE_MANIFEST.json", manifest)
    files["commands"] = write_jsonl(
        run_dir / "COMMANDS.jsonl", package.get("command_evidence") or []
    )
    files["approvals"] = write_jsonl(
        run_dir / "APPROVALS.jsonl", package.get("approval_requests") or []
    )
    files["reviews"] = write_text(run_dir / "REVIEWS.md", format_reviews_markdown(package))
    files["decisions"] = write_text(run_dir / "DECISIONS.md", format_decisions_markdown(package))
    files["readiness"] = write_text(run_dir / "READINESS.md", format_readiness_markdown(package))
    if write_result:
        files["result"] = write_text(run_dir / "RESULT.md", summary_markdown)

    return {
        "schema": MANIFEST_SCHEMA,
        "status": "ok",
        "code": None,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "files": files,
        "manifest": manifest,
    }


def build_manifest(package: dict[str, Any]) -> dict[str, Any]:
    run = package.get("run") or {}
    readiness = package.get("readiness") or {}
    evidence_files = [
        "EVIDENCE_MANIFEST.json",
        "COMMANDS.jsonl",
        "APPROVALS.jsonl",
        "REVIEWS.md",
        "DECISIONS.md",
        "READINESS.md",
    ]
    return {
        "schema": MANIFEST_SCHEMA,
        "run_id": run.get("run_id"),
        "goal_id": run.get("goal_id"),
        "workspace_id": run.get("workspace_id"),
        "title": run.get("title"),
        "created_at": run.get("created_at"),
        "privacy": "local-only",
        "risk_level": run.get("risk_level"),
        "status": run.get("status"),
        "orchestrator_mode": "cli-first",
        "readiness_status": readiness.get("status"),
        "evidence_files": evidence_files,
        "db_event_refs": collect_db_event_refs(package),
        "workers": package.get("workers") or [],
        "pending_approvals": [
            approval
            for approval in package.get("approval_requests") or []
            if approval.get("status") == "requested"
        ],
        "open_findings": [
            finding
            for finding in package.get("review_findings") or []
            if finding.get("status") == "open"
        ],
    }


def collect_db_event_refs(package: dict[str, Any]) -> list[dict[str, str | None]]:
    refs: list[dict[str, str | None]] = []
    for item in package.get("command_evidence") or []:
        refs.append({"type": "command_evidence", "id": item.get("command_evidence_id")})
    for item in package.get("review_findings") or []:
        refs.append({"type": "review_finding", "id": item.get("finding_id")})
    for item in package.get("approval_requests") or []:
        refs.append({"type": "approval_request", "id": item.get("approval_id")})
    for item in package.get("decisions") or []:
        refs.append({"type": "decision", "id": item.get("decision_id")})
    return refs


def write_json(path: Path, payload: dict[str, Any]) -> str:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return str(path)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> str:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    return str(path)


def write_text(path: Path, text: str) -> str:
    path.write_text(text.rstrip() + "\n", encoding="utf-8")
    return str(path)


def format_reviews_markdown(package: dict[str, Any]) -> str:
    lines = ["# Review Findings", ""]
    findings = package.get("review_findings") or []
    if not findings:
        lines.append("- none")
    for finding in findings:
        lines.append(
            f"- {finding.get('severity')} {finding.get('status')}: "
            f"{finding.get('summary')} (`{finding.get('finding_id')}`)"
        )
    return "\n".join(lines)


def format_decisions_markdown(package: dict[str, Any]) -> str:
    lines = ["# Decisions", ""]
    decisions = package.get("decisions") or []
    if not decisions:
        lines.append("- none")
    for decision in decisions:
        lines.append(
            f"- {decision.get('status')}: {decision.get('decision')} "
            f"(`{decision.get('decision_id')}`)"
        )
    return "\n".join(lines)


def format_readiness_markdown(package: dict[str, Any]) -> str:
    readiness = package.get("readiness") or {}
    lines = [
        "# Readiness",
        "",
        f"- Status: `{readiness.get('status')}`",
        f"- Confidence: `{readiness.get('confidence')}`",
        f"- Recommended action: `{readiness.get('recommended_action')}`",
        "",
        "## Blocking Reasons",
    ]
    reasons = readiness.get("blocking_reasons") or []
    lines.extend(f"- {reason}" for reason in reasons)
    if not reasons:
        lines.append("- none")
    return "\n".join(lines)
