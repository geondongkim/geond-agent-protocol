from __future__ import annotations

import re
from typing import Any

from psycopg import Connection

from geond.storage.repository import (
    get_workspace_coordination_policy,
    list_active_file_reservations,
    list_active_symbol_reservations,
    list_handoff_summaries,
    resolve_workspace_id,
)
from geond.storage.resources import get_workspace_lineage


def review_workspace_context(
    conn: Connection,
    workspace_id_or_uri: str,
    intent: str = "",
    file_paths: list[str] | None = None,
    symbols: list[str] | None = None,
    agent_name: str | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    workspace_id = resolve_workspace_id(conn, workspace_id_or_uri)
    if not workspace_id:
        return {
            "workspace_id_or_uri": workspace_id_or_uri,
            "status": "workspace_not_found",
            "recommendations": ["Register or import the workspace before reviewing context."],
        }

    requested_files = normalized_list(file_paths)
    requested_symbols = normalized_list(symbols)
    policy = get_workspace_coordination_policy(conn, workspace_id)
    file_reservations = list_active_file_reservations(
        conn,
        workspace_id,
        requested_files or None,
    )[:limit]
    symbol_reservations = list_active_symbol_reservations(
        conn,
        workspace_id,
        requested_symbols or None,
    )[:limit]
    handoffs = list_handoff_summaries(conn, workspace_id, status="open", limit=limit)
    lineage = get_workspace_lineage(conn, workspace_id, limit=max(limit * 4, limit))
    matches = context_matches(intent, requested_files, requested_symbols, handoffs, lineage, limit)
    assessment = context_assessment(
        policy["reservation_conflict_policy"],
        file_reservations,
        symbol_reservations,
        agent_name,
        handoffs,
        matches,
    )
    return {
        "workspace_id": workspace_id,
        "workspace_uri": policy.get("workspace_uri"),
        "agent_name": agent_name,
        "requested": {
            "intent": intent,
            "file_paths": requested_files,
            "symbols": requested_symbols,
        },
        "policy": policy,
        "loaded_context": {
            "file_reservations": file_reservations,
            "symbol_reservations": symbol_reservations,
            "open_handoffs": handoffs,
            "lineage_nodes": lineage.get("nodes", [])[:limit],
            "lineage_edges": lineage.get("edges", [])[:limit],
        },
        "matches": matches,
        "assessment": assessment,
        "recommendations": context_recommendations(
            assessment,
            requested_files,
            requested_symbols,
            matches,
        ),
    }


def normalized_list(values: list[str] | None) -> list[str]:
    return [value.strip() for value in (values or []) if value and value.strip()]


def context_assessment(
    policy: str,
    file_reservations: list[dict[str, Any]],
    symbol_reservations: list[dict[str, Any]],
    agent_name: str | None,
    handoffs: list[dict[str, Any]],
    matches: list[dict[str, Any]],
) -> dict[str, Any]:
    own_files, external_files = partition_reservations(file_reservations, agent_name)
    own_symbols, external_symbols = partition_reservations(symbol_reservations, agent_name)
    conflict_count = len(external_files) + len(external_symbols)
    if conflict_count == 0:
        status = "clear"
    elif policy == "strict":
        status = "blocked_by_policy"
    elif policy == "override-with-reason":
        status = "override_reason_required"
    else:
        status = "advisory_conflicts"
    return {
        "status": status,
        "reservation_conflict_policy": policy,
        "external_conflict_count": conflict_count,
        "own_reservation_count": len(own_files) + len(own_symbols),
        "open_handoff_count": len(handoffs),
        "matched_context_count": len(matches),
        "needs_handoff": len(handoffs) == 0 or len(matches) == 0,
    }


def partition_reservations(
    reservations: list[dict[str, Any]],
    agent_name: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not agent_name:
        return [], reservations
    own = [item for item in reservations if item.get("agent_name") == agent_name]
    external = [item for item in reservations if item.get("agent_name") != agent_name]
    return own, external


def context_recommendations(
    assessment: dict[str, Any],
    file_paths: list[str],
    symbols: list[str],
    matches: list[dict[str, Any]],
) -> list[str]:
    recommendations: list[str] = []
    status = assessment["status"]
    if status == "blocked_by_policy":
        recommendations.append("Resolve or release conflicting reservations before starting work.")
    elif status == "override_reason_required":
        recommendations.append(
            "Capture an explicit override reason before reserving conflicting work."
        )
    elif status == "advisory_conflicts":
        recommendations.append("Review advisory conflicts and record the coordination decision.")
    if file_paths:
        recommendations.append(
            "Reserve requested files or confirm an existing reservation covers them."
        )
    if symbols:
        recommendations.append("Check symbol conflicts before editing referenced definitions.")
    if matches:
        recommendations.append("Start from the highest scoring handoff or lineage match.")
    if assessment["needs_handoff"]:
        recommendations.append(
            "Record a structured handoff after this work with tested commands and risks."
        )
    return recommendations


def context_matches(
    intent: str,
    file_paths: list[str],
    symbols: list[str],
    handoffs: list[dict[str, Any]],
    lineage: dict[str, Any],
    limit: int,
) -> list[dict[str, Any]]:
    needles = set(query_tokens(intent))
    needles.update(query_tokens(" ".join(file_paths)))
    needles.update(query_tokens(" ".join(symbols)))
    candidates: list[dict[str, Any]] = []
    for handoff in handoffs:
        text = handoff_match_text(handoff)
        candidates.append(
            {
                "kind": "handoff_summary",
                "id": handoff.get("handoff_id"),
                "title": handoff.get("summary"),
                "score": match_score(needles, text),
            }
        )
    for node in lineage.get("nodes", []):
        if node.get("kind") == "agent":
            continue
        text = " ".join(
            str(part)
            for part in (node.get("title"), node.get("source"), node.get("status"))
            if part
        )
        candidates.append(
            {
                "kind": node.get("kind"),
                "id": node.get("raw_id"),
                "title": node.get("title"),
                "score": match_score(needles, text),
            }
        )
    ranked = [item for item in candidates if item["score"] > 0]
    return sorted(ranked, key=lambda item: item["score"], reverse=True)[:limit]


def format_context_review_markdown(review: dict[str, Any]) -> str:
    if review.get("status") == "workspace_not_found":
        recommendations = "\n".join(f"- {item}" for item in review.get("recommendations", []))
        return (
            "# Context Review\n\n"
            f"- Workspace: `{review.get('workspace_id_or_uri')}`\n"
            "- Status: `workspace_not_found`\n\n"
            "## Recommendations\n"
            f"{recommendations}\n"
        )

    assessment = review.get("assessment") or {}
    requested = review.get("requested") or {}
    loaded = review.get("loaded_context") or {}
    lines = [
        "# Context Review",
        "",
        f"- Workspace: `{review.get('workspace_uri') or review.get('workspace_id')}`",
        f"- Status: `{assessment.get('status')}`",
        f"- Policy: `{assessment.get('reservation_conflict_policy')}`",
        f"- Intent: {requested.get('intent') or ''}",
        f"- Files: {format_inline_list(requested.get('file_paths') or [])}",
        f"- Symbols: {format_inline_list(requested.get('symbols') or [])}",
        "",
        "## Loaded Context",
        "",
        f"- File reservations: `{len(loaded.get('file_reservations') or [])}`",
        f"- Symbol reservations: `{len(loaded.get('symbol_reservations') or [])}`",
        f"- Open handoffs: `{len(loaded.get('open_handoffs') or [])}`",
        f"- Lineage nodes shown: `{len(loaded.get('lineage_nodes') or [])}`",
        "",
        "## Matches",
        "",
    ]
    matches = review.get("matches") or []
    if matches:
        lines.extend(
            f"- `{item.get('kind')}` {item.get('title') or item.get('id')} "
            f"(score {item.get('score')})"
            for item in matches
        )
    else:
        lines.append("- No matching handoff or lineage context found.")

    lines.extend(["", "## Recommendations", ""])
    recommendations = review.get("recommendations") or []
    if recommendations:
        lines.extend(f"- {item}" for item in recommendations)
    else:
        lines.append("- No follow-up required.")
    return "\n".join(lines)


def format_inline_list(values: list[str]) -> str:
    return ", ".join(f"`{value}`" for value in values) if values else "`none`"


def handoff_match_text(handoff: dict[str, Any]) -> str:
    metadata = handoff.get("metadata") or {}
    template = metadata.get("handoff_template") if isinstance(metadata, dict) else {}
    parts = [
        handoff.get("summary"),
        " ".join(handoff.get("next_steps") or []),
        " ".join(handoff.get("blocked_on") or []),
    ]
    if isinstance(template, dict):
        parts.extend(
            [
                " ".join(template.get("tested_commands") or []),
                " ".join(template.get("remaining_risks") or []),
                template.get("next_action"),
            ]
        )
    return " ".join(str(part) for part in parts if part)


def match_score(needles: set[str], text: str) -> float:
    haystack = set(query_tokens(text))
    if not needles or not haystack:
        return 0.0
    return round(len(needles & haystack) / len(needles), 6)


def query_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for raw_token in re.findall(r"\w+", text.casefold(), flags=re.UNICODE):
        token = raw_token.strip("_")
        if not token:
            continue
        tokens.append(token)
        tokens.extend(part for part in token.split("_") if part)
    return sorted(set(tokens))
