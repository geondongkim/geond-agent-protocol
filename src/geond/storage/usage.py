from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb

from geond.adapters.claude_code import ParsedClaudeCodeSession
from geond.adapters.codex import ParsedCodexSession
from geond.adapters.vscode_copilot import ParsedCopilotSession
from geond.storage.pricing import estimate_usage_cost_usd, lookup_model_pricing
from geond.storage.repository import resolve_session_row_id, resolve_workspace_id, upsert_agent


def insert_usage_event(
    conn: Connection,
    *,
    workspace_id: str,
    source: str,
    session_id: str | None = None,
    session_external_id: str | None = None,
    agent_id: str | None = None,
    agent_name: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    operation: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cached_input_tokens: int | None = None,
    reasoning_tokens: int | None = None,
    total_tokens: int | None = None,
    estimated: bool = False,
    estimated_cost_usd: Decimal | float | str | None = None,
    priced_at: object | None = None,
    source_record_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    resolved_session_id = resolve_session_row_id(
        conn,
        workspace_id,
        session_id=session_id,
        session_external_id=session_external_id,
    )
    resolved_agent_id = agent_id or (upsert_agent(conn, agent_name) if agent_name else None)
    resolved_total_tokens = total_tokens
    if resolved_total_tokens is None:
        resolved_total_tokens = sum_known_tokens(
            input_tokens,
            output_tokens,
            cached_input_tokens,
            reasoning_tokens,
        )
    resolved_estimated_cost_usd = estimated_cost_usd
    resolved_priced_at = priced_at
    if resolved_estimated_cost_usd is None:
        pricing_timestamp = priced_at or datetime.now(UTC)
        price = lookup_model_pricing(
            conn,
            provider=provider,
            model=model,
            at=pricing_timestamp if isinstance(pricing_timestamp, datetime) else None,
        )
        resolved_estimated_cost_usd = estimate_usage_cost_usd(
            price,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            reasoning_tokens=reasoning_tokens,
        )
        if resolved_estimated_cost_usd is not None:
            resolved_priced_at = pricing_timestamp

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO llm_usage_events (
                workspace_id,
                session_id,
                agent_id,
                source,
                provider,
                model,
                operation,
                input_tokens,
                output_tokens,
                cached_input_tokens,
                reasoning_tokens,
                total_tokens,
                estimated,
                estimated_cost_usd,
                priced_at,
                source_record_id,
                metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source, source_record_id) WHERE source_record_id IS NOT NULL
            DO UPDATE SET
                workspace_id = EXCLUDED.workspace_id,
                session_id = EXCLUDED.session_id,
                agent_id = EXCLUDED.agent_id,
                provider = EXCLUDED.provider,
                model = EXCLUDED.model,
                operation = EXCLUDED.operation,
                input_tokens = EXCLUDED.input_tokens,
                output_tokens = EXCLUDED.output_tokens,
                cached_input_tokens = EXCLUDED.cached_input_tokens,
                reasoning_tokens = EXCLUDED.reasoning_tokens,
                total_tokens = EXCLUDED.total_tokens,
                estimated = EXCLUDED.estimated,
                estimated_cost_usd = EXCLUDED.estimated_cost_usd,
                priced_at = EXCLUDED.priced_at,
                metadata = EXCLUDED.metadata
            RETURNING id::text
            """,
            (
                workspace_id,
                resolved_session_id,
                resolved_agent_id,
                source,
                provider,
                model,
                operation,
                input_tokens,
                output_tokens,
                cached_input_tokens,
                reasoning_tokens,
                resolved_total_tokens,
                estimated,
                resolved_estimated_cost_usd,
                resolved_priced_at,
                source_record_id,
                Jsonb(metadata or {}),
            ),
        )
        usage_id = cur.fetchone()[0]
    conn.commit()
    return usage_id


def summarize_usage(
    conn: Connection,
    workspace_id_or_uri: str,
    *,
    source: str | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    workspace_id = resolve_workspace_id(conn, workspace_id_or_uri)
    if not workspace_id:
        return {
            "workspace_id_or_uri": workspace_id_or_uri,
            "status": "workspace_not_found",
            "totals": {},
            "by_source": [],
            "by_model": [],
        }

    where_clause, params = usage_filter_sql(workspace_id, source, provider, model)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT
                count(*) AS event_count,
                coalesce(sum(total_tokens), 0) AS total_tokens,
                count(*) FILTER (WHERE estimated) AS estimated_event_count,
                count(*) FILTER (WHERE NOT estimated) AS exact_event_count,
                coalesce(sum(total_tokens) FILTER (WHERE estimated), 0) AS estimated_tokens,
                coalesce(sum(total_tokens) FILTER (WHERE NOT estimated), 0) AS exact_tokens,
                sum(estimated_cost_usd) AS estimated_cost_usd
            FROM llm_usage_events
            {where_clause}
            """,
            params,
        )
        totals = usage_total_result(cur.fetchone())
        cur.execute(
            f"""
            SELECT
                source,
                count(*) AS event_count,
                coalesce(sum(total_tokens), 0) AS total_tokens,
                count(*) FILTER (WHERE estimated) AS estimated_event_count,
                sum(estimated_cost_usd) AS estimated_cost_usd
            FROM llm_usage_events
            {where_clause}
            GROUP BY source
            ORDER BY total_tokens DESC, event_count DESC, source
            """,
            params,
        )
        by_source = [usage_group_result(row, ["source"]) for row in cur.fetchall()]
        cur.execute(
            f"""
            SELECT
                provider,
                model,
                count(*) AS event_count,
                coalesce(sum(total_tokens), 0) AS total_tokens,
                count(*) FILTER (WHERE estimated) AS estimated_event_count,
                sum(estimated_cost_usd) AS estimated_cost_usd
            FROM llm_usage_events
            {where_clause}
            GROUP BY provider, model
            ORDER BY total_tokens DESC, event_count DESC, provider NULLS LAST, model NULLS LAST
            """,
            params,
        )
        by_model = [usage_group_result(row, ["provider", "model"]) for row in cur.fetchall()]
        cur.execute(
            f"""
            SELECT
                coalesce(agents.name, llm_usage_events.source, 'unknown') AS agent_name,
                count(*) AS event_count,
                coalesce(sum(total_tokens), 0) AS total_tokens,
                count(*) FILTER (WHERE estimated) AS estimated_event_count,
                sum(estimated_cost_usd) AS estimated_cost_usd
            FROM llm_usage_events
            LEFT JOIN agents ON agents.id = llm_usage_events.agent_id
            {where_clause}
            GROUP BY coalesce(agents.name, llm_usage_events.source, 'unknown')
            ORDER BY total_tokens DESC, event_count DESC, agent_name
            """,
            params,
        )
        by_agent = [usage_group_result(row, ["agent_name"]) for row in cur.fetchall()]

    return {
        "status": "ok",
        "workspace_id": workspace_id,
        "filters": {"source": source, "provider": provider, "model": model},
        "totals": totals,
        "by_source": by_source,
        "by_model": by_model,
        "by_agent": by_agent,
        "data_quality": {
            "exact_event_count": totals["exact_event_count"],
            "estimated_event_count": totals["estimated_event_count"],
            "exact_token_share": token_share(totals["exact_tokens"], totals["total_tokens"]),
            "estimated_token_share": token_share(
                totals["estimated_tokens"], totals["total_tokens"]
            ),
        },
    }


def record_codex_usage_events(
    conn: Connection,
    *,
    workspace_id: str,
    session: ParsedCodexSession,
    session_row_id: str | None = None,
) -> list[str]:
    if not usage_table_exists(conn):
        return []

    provider = string_or_none(session.metadata.get("model_provider"))
    model = string_or_none(session.metadata.get("model"))
    usage_ids: list[str] = []
    for event in session.events:
        for usage_index, usage in enumerate(extract_token_usage_candidates(event.raw)):
            if not has_token_usage(usage):
                continue
            usage_ids.append(
                insert_usage_event(
                    conn,
                    workspace_id=workspace_id,
                    session_id=session_row_id,
                    agent_name="codex",
                    source="codex",
                    provider=provider,
                    model=model,
                    operation=event.event_type,
                    input_tokens=usage.get("input_tokens"),
                    output_tokens=usage.get("output_tokens"),
                    cached_input_tokens=usage.get("cached_input_tokens"),
                    reasoning_tokens=usage.get("reasoning_tokens"),
                    total_tokens=usage.get("total_tokens"),
                    estimated=False,
                    source_record_id=(
                        f"codex:{session.session_id}:{event.ordinal}:usage:{usage_index}"
                    ),
                    metadata={
                        "source": "codex_import",
                        "accuracy": "provider_reported",
                        "event_ordinal": event.ordinal,
                    },
                )
            )

    if usage_ids:
        return usage_ids

    estimated = estimate_codex_message_usage(session)
    if not has_token_usage(estimated):
        return []
    return [
        insert_usage_event(
            conn,
            workspace_id=workspace_id,
            session_id=session_row_id,
            agent_name="codex",
            source="codex",
            provider=provider,
            model=model,
            operation="session_message_estimate",
            input_tokens=estimated.get("input_tokens"),
            output_tokens=estimated.get("output_tokens"),
            total_tokens=estimated.get("total_tokens"),
            estimated=True,
            source_record_id=f"codex:{session.session_id}:estimated_messages",
            metadata={
                "source": "codex_import",
                "accuracy": "adapter_estimated",
                "estimator": "ceil_char_count_div_4_by_role",
                "message_count": len(session.messages),
            },
        )
    ]


def record_claude_code_usage_events(
    conn: Connection,
    *,
    workspace_id: str,
    session: ParsedClaudeCodeSession,
    session_row_id: str | None = None,
) -> list[str]:
    if not usage_table_exists(conn):
        return []

    provider = "anthropic"
    model = claude_model_from_session(session)
    usage_ids: list[str] = []
    for event in session.events:
        for usage_index, usage in enumerate(extract_token_usage_candidates(event.raw)):
            if not has_token_usage(usage):
                continue
            usage_ids.append(
                insert_usage_event(
                    conn,
                    workspace_id=workspace_id,
                    session_id=session_row_id,
                    agent_name="claude-code",
                    source="claude-code",
                    provider=provider,
                    model=model,
                    operation=event.record_type,
                    input_tokens=usage.get("input_tokens"),
                    output_tokens=usage.get("output_tokens"),
                    cached_input_tokens=usage.get("cached_input_tokens"),
                    reasoning_tokens=usage.get("reasoning_tokens"),
                    total_tokens=usage.get("total_tokens"),
                    estimated=False,
                    source_record_id=(
                        f"claude-code:{session.session_id}:{event.ordinal}:usage:{usage_index}"
                    ),
                    metadata={
                        "source": "claude_code_import",
                        "accuracy": "provider_reported",
                        "event_ordinal": event.ordinal,
                    },
                )
            )

    if usage_ids:
        return usage_ids

    estimated = estimate_claude_code_message_usage(session)
    if not has_token_usage(estimated):
        return []
    return [
        insert_usage_event(
            conn,
            workspace_id=workspace_id,
            session_id=session_row_id,
            agent_name="claude-code",
            source="claude-code",
            provider=provider,
            model=model,
            operation="session_message_estimate",
            input_tokens=estimated.get("input_tokens"),
            output_tokens=estimated.get("output_tokens"),
            total_tokens=estimated.get("total_tokens"),
            estimated=True,
            source_record_id=f"claude-code:{session.session_id}:estimated_messages",
            metadata={
                "source": "claude_code_import",
                "accuracy": "adapter_estimated",
                "estimator": "ceil_char_count_div_4_by_role",
                "message_count": len(session.messages),
                "excludes": ["thinking_blocks", "tool_only_blocks"],
            },
        )
    ]


def record_vscode_copilot_usage_events(
    conn: Connection,
    *,
    workspace_id: str,
    session: ParsedCopilotSession,
    session_row_id: str | None = None,
) -> list[str]:
    if not usage_table_exists(conn):
        return []

    provider = "github"
    model = vscode_copilot_model_from_session(session)
    usage_ids: list[str] = []
    for event_source, ordinal, operation, raw in vscode_copilot_raw_events(session):
        for usage_index, usage in enumerate(extract_token_usage_candidates(raw)):
            if not has_token_usage(usage):
                continue
            usage_ids.append(
                insert_usage_event(
                    conn,
                    workspace_id=workspace_id,
                    session_id=session_row_id,
                    agent_name="vscode-copilot",
                    source="vscode-copilot",
                    provider=provider,
                    model=model,
                    operation=operation,
                    input_tokens=usage.get("input_tokens"),
                    output_tokens=usage.get("output_tokens"),
                    cached_input_tokens=usage.get("cached_input_tokens"),
                    reasoning_tokens=usage.get("reasoning_tokens"),
                    total_tokens=usage.get("total_tokens"),
                    estimated=False,
                    source_record_id=(
                        "vscode-copilot:"
                        f"{session.session_id}:{event_source}:{ordinal}:usage:{usage_index}"
                    ),
                    metadata={
                        "source": "vscode_copilot_import",
                        "accuracy": "provider_reported",
                        "event_source": event_source,
                        "event_ordinal": ordinal,
                    },
                )
            )

    if usage_ids:
        return usage_ids

    estimated = estimate_vscode_copilot_message_usage(session)
    if not has_token_usage(estimated):
        return []
    return [
        insert_usage_event(
            conn,
            workspace_id=workspace_id,
            session_id=session_row_id,
            agent_name="vscode-copilot",
            source="vscode-copilot",
            provider=provider,
            model=model,
            operation="session_message_estimate",
            input_tokens=estimated.get("input_tokens"),
            output_tokens=estimated.get("output_tokens"),
            total_tokens=estimated.get("total_tokens"),
            estimated=True,
            source_record_id=f"vscode-copilot:{session.session_id}:estimated_messages",
            metadata={
                "source": "vscode_copilot_import",
                "accuracy": "session_estimated",
                "estimator": "ceil_char_count_div_4_by_kind",
                "chat_line_count": len(session.chat_lines),
                "transcript_event_count": len(session.transcript_events),
            },
        )
    ]


def format_usage_summary_markdown(summary: dict[str, Any]) -> str:
    if summary.get("status") == "workspace_not_found":
        return (
            "# Usage Summary\n\n"
            f"- Workspace: `{summary.get('workspace_id_or_uri')}`\n"
            "- Status: `workspace_not_found`\n"
        )

    totals = summary.get("totals") or {}
    quality = summary.get("data_quality") or {}
    lines = [
        "# Usage Summary",
        "",
        f"- Workspace: `{summary.get('workspace_id')}`",
        f"- Events: `{totals.get('event_count', 0)}`",
        f"- Tokens: `{totals.get('total_tokens', 0)}`",
        f"- Estimated cost USD: `{totals.get('estimated_cost_usd')}`",
        f"- Exact token share: `{quality.get('exact_token_share')}`",
        f"- Estimated token share: `{quality.get('estimated_token_share')}`",
        "",
        "## By Source",
        "",
    ]
    by_source = summary.get("by_source") or []
    if by_source:
        lines.extend(
            f"- `{row.get('source')}`: `{row.get('total_tokens')}` tokens "
            f"across `{row.get('event_count')}` events"
            for row in by_source
        )
    else:
        lines.append("- No usage events recorded.")
    return "\n".join(lines)


def usage_group_report(summary: dict[str, Any], group_key: str) -> dict[str, Any]:
    return {
        "status": summary.get("status"),
        "workspace_id": summary.get("workspace_id"),
        "workspace_id_or_uri": summary.get("workspace_id_or_uri"),
        "filters": summary.get("filters", {}),
        "totals": summary.get("totals", {}),
        group_key: summary.get(group_key, []),
        "data_quality": summary.get("data_quality", {}),
    }


def format_usage_group_markdown(
    report: dict[str, Any],
    *,
    title: str,
    group_key: str,
    label_keys: list[str],
) -> str:
    if report.get("status") == "workspace_not_found":
        return (
            f"# {title}\n\n"
            f"- Workspace: `{report.get('workspace_id_or_uri')}`\n"
            "- Status: `workspace_not_found`\n"
        )

    totals = report.get("totals") or {}
    lines = [
        f"# {title}",
        "",
        f"- Workspace: `{report.get('workspace_id')}`",
        f"- Events: `{totals.get('event_count', 0)}`",
        f"- Tokens: `{totals.get('total_tokens', 0)}`",
        "",
    ]
    rows = report.get(group_key) or []
    if not rows:
        lines.append("- No usage events recorded.")
        return "\n".join(lines)

    for row in rows:
        label = " / ".join(str(row.get(key) or "unknown") for key in label_keys)
        lines.append(
            f"- `{label}`: `{row.get('total_tokens')}` tokens "
            f"across `{row.get('event_count')}` events"
        )
    return "\n".join(lines)


def build_usage_risk_signals(payload: dict[str, Any]) -> dict[str, Any]:
    usage = payload.get("usage") or payload
    totals = usage.get("totals") or {}
    quality = usage.get("data_quality") or {}
    evidence = payload.get("evidence") or {}
    linked = payload.get("usage_vs_evidence") or {}
    event_count = int(totals.get("event_count") or 0)
    total_tokens = int(totals.get("total_tokens") or 0)
    estimated_share = quality.get("estimated_token_share")
    exact_event_count = int(quality.get("exact_event_count") or 0)
    user_prompts = int(evidence.get("user_prompts") or 0)
    has_output_evidence = bool(linked.get("has_output_evidence"))
    signals: list[dict[str, Any]] = []

    if event_count == 0:
        signals.append(
            {
                "severity": "info",
                "code": "no_usage_events",
                "message": "No usage events have been recorded for this workspace.",
            }
        )
    if total_tokens > 0 and not has_output_evidence:
        signals.append(
            {
                "severity": "warning",
                "code": "usage_without_output_evidence",
                "message": "Usage exists without linked changesets or tested handoffs.",
            }
        )
    if estimated_share is not None and estimated_share >= 0.8:
        signals.append(
            {
                "severity": "warning",
                "code": "estimated_heavy_usage",
                "message": (
                    "Most token counts are estimates; review precision before cost reporting."
                ),
                "estimated_token_share": estimated_share,
            }
        )
    if event_count > 0 and exact_event_count == 0:
        signals.append(
            {
                "severity": "info",
                "code": "no_exact_usage_events",
                "message": "All usage events are estimated rather than provider-reported.",
            }
        )
    if user_prompts > 0 and total_tokens == 0:
        signals.append(
            {
                "severity": "warning",
                "code": "prompt_evidence_without_usage",
                "message": "Session evidence exists without matching usage events.",
            }
        )
    if not signals:
        signals.append(
            {
                "severity": "ok",
                "code": "usage_evidence_linked",
                "message": "Usage signals are linked to evidence for this workspace.",
            }
        )

    return {
        "status": payload.get("status") or usage.get("status"),
        "workspace_id": payload.get("workspace_id") or usage.get("workspace_id"),
        "totals": totals,
        "evidence": evidence,
        "usage_vs_evidence": linked,
        "signals": signals,
    }


def format_usage_risk_signals_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Usage Risk Signals",
        "",
        f"- Workspace: `{report.get('workspace_id')}`",
        "",
    ]
    for signal in report.get("signals") or []:
        lines.append(
            f"- `{signal.get('severity')}` `{signal.get('code')}`: {signal.get('message')}"
        )
    return "\n".join(lines)


def usage_filter_sql(
    workspace_id: str,
    source: str | None,
    provider: str | None,
    model: str | None,
) -> tuple[str, list[Any]]:
    filters = ["workspace_id = %s"]
    params: list[Any] = [workspace_id]
    if source:
        filters.append("source = %s")
        params.append(source)
    if provider:
        filters.append("provider = %s")
        params.append(provider)
    if model:
        filters.append("model = %s")
        params.append(model)
    return "WHERE " + " AND ".join(filters), params


def usage_table_exists(conn: Connection) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.llm_usage_events') IS NOT NULL")
        return bool(cur.fetchone()[0])


def extract_token_usage_candidates(value: Any) -> list[dict[str, int | None]]:
    raw_candidates = find_token_usage_dicts(value)
    return [normalize_token_usage(candidate) for candidate in raw_candidates]


def find_token_usage_dicts(value: Any, max_depth: int = 8) -> list[dict[str, Any]]:
    if max_depth <= 0:
        return []
    if isinstance(value, list):
        candidates: list[dict[str, Any]] = []
        for item in value:
            candidates.extend(find_token_usage_dicts(item, max_depth - 1))
        return candidates
    if not isinstance(value, dict):
        return []

    usage = value.get("usage")
    if isinstance(usage, dict):
        return [usage]
    if has_usage_shape(value):
        return [value]

    candidates = []
    for item in value.values():
        candidates.extend(find_token_usage_dicts(item, max_depth - 1))
    return candidates


def normalize_token_usage(value: dict[str, Any]) -> dict[str, int | None]:
    input_tokens = first_int(value, "input_tokens", "prompt_tokens", "input_token_count")
    output_tokens = first_int(
        value,
        "output_tokens",
        "completion_tokens",
        "output_token_count",
        "completion_token_count",
    )
    cached_input_tokens = first_int(
        value,
        "cached_input_tokens",
        "cache_read_input_tokens",
        "cached_tokens",
    )
    reasoning_tokens = first_int(value, "reasoning_tokens")
    total_tokens = first_int(value, "total_tokens", "total_token_count")

    input_details = first_dict(value, "input_tokens_details", "prompt_tokens_details")
    if input_details and cached_input_tokens is None:
        cached_input_tokens = first_int(input_details, "cached_tokens")
    output_details = first_dict(value, "output_tokens_details", "completion_tokens_details")
    if output_details and reasoning_tokens is None:
        reasoning_tokens = first_int(output_details, "reasoning_tokens")

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_input_tokens": cached_input_tokens,
        "reasoning_tokens": reasoning_tokens,
        "total_tokens": total_tokens,
    }


def has_usage_shape(value: dict[str, Any]) -> bool:
    usage_keys = {
        "input_tokens",
        "prompt_tokens",
        "input_token_count",
        "output_tokens",
        "completion_tokens",
        "output_token_count",
        "completion_token_count",
        "total_tokens",
        "total_token_count",
    }
    return any(key in value for key in usage_keys)


def has_token_usage(value: dict[str, int | None]) -> bool:
    return any(token_count is not None for token_count in value.values())


def estimate_codex_message_usage(session: ParsedCodexSession) -> dict[str, int | None]:
    input_tokens = 0
    output_tokens = 0
    for message in session.messages:
        estimated_tokens = estimate_text_tokens(message.content)
        if message.role == "assistant":
            output_tokens += estimated_tokens
        else:
            input_tokens += estimated_tokens
    total_tokens = input_tokens + output_tokens
    return {
        "input_tokens": input_tokens or None,
        "output_tokens": output_tokens or None,
        "cached_input_tokens": None,
        "reasoning_tokens": None,
        "total_tokens": total_tokens or None,
    }


def estimate_claude_code_message_usage(
    session: ParsedClaudeCodeSession,
) -> dict[str, int | None]:
    input_tokens = 0
    output_tokens = 0
    for message in session.messages:
        estimated_tokens = estimate_text_tokens(message.content)
        if message.role == "assistant":
            output_tokens += estimated_tokens
        else:
            input_tokens += estimated_tokens
    total_tokens = input_tokens + output_tokens
    return {
        "input_tokens": input_tokens or None,
        "output_tokens": output_tokens or None,
        "cached_input_tokens": None,
        "reasoning_tokens": None,
        "total_tokens": total_tokens or None,
    }


def claude_model_from_session(session: ParsedClaudeCodeSession) -> str | None:
    metadata_model = string_or_none(session.metadata.get("model"))
    if metadata_model:
        return metadata_model
    for event in session.events:
        model = string_or_none(event.raw.get("model"))
        if model:
            return model
        message = event.raw.get("message")
        if isinstance(message, dict):
            model = string_or_none(message.get("model"))
            if model:
                return model
    return None


def estimate_vscode_copilot_message_usage(
    session: ParsedCopilotSession,
) -> dict[str, int | None]:
    input_tokens = 0
    output_tokens = 0
    if session.chat_lines:
        for line in session.chat_lines:
            estimated_tokens = estimate_text_tokens(line.content)
            if line.kind == 2:
                output_tokens += estimated_tokens
            else:
                input_tokens += estimated_tokens
    else:
        for event in session.transcript_events:
            estimated_tokens = estimate_text_tokens(event.content)
            if event.event_type in {"agentMessage", "assistantMessage"}:
                output_tokens += estimated_tokens
            elif event.event_type == "userMessage":
                input_tokens += estimated_tokens
    total_tokens = input_tokens + output_tokens
    return {
        "input_tokens": input_tokens or None,
        "output_tokens": output_tokens or None,
        "cached_input_tokens": None,
        "reasoning_tokens": None,
        "total_tokens": total_tokens or None,
    }


def vscode_copilot_raw_events(
    session: ParsedCopilotSession,
) -> list[tuple[str, int, str, dict[str, Any]]]:
    events: list[tuple[str, int, str, dict[str, Any]]] = []
    events.extend(
        ("chat", line.ordinal, f"chat.kind.{line.kind}", line.raw) for line in session.chat_lines
    )
    events.extend(
        ("transcript", event.ordinal, event.event_type, event.raw)
        for event in session.transcript_events
    )
    return events


def vscode_copilot_model_from_session(session: ParsedCopilotSession) -> str | None:
    model = string_or_none(session.index_entry.get("model"))
    if model:
        return model
    for _, _, _, raw in vscode_copilot_raw_events(session):
        model = string_or_none(raw.get("model"))
        if model:
            return model
        data = raw.get("data")
        if isinstance(data, dict):
            model = string_or_none(data.get("model"))
            if model:
                return model
    return None


def estimate_text_tokens(text: str | None) -> int:
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def first_int(value: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, bool):
            continue
        if isinstance(candidate, int):
            return candidate
        if isinstance(candidate, float) and candidate.is_integer():
            return int(candidate)
        if isinstance(candidate, str) and candidate.isdigit():
            return int(candidate)
    return None


def first_dict(value: dict[str, Any], *keys: str) -> dict[str, Any] | None:
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, dict):
            return candidate
    return None


def string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def usage_total_result(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "event_count": row[0],
        "total_tokens": row[1],
        "estimated_event_count": row[2],
        "exact_event_count": row[3],
        "estimated_tokens": row[4],
        "exact_tokens": row[5],
        "estimated_cost_usd": numeric_value(row[6]),
    }


def usage_group_result(row: tuple[Any, ...], labels: list[str]) -> dict[str, Any]:
    offset = len(labels)
    result = {label: row[index] for index, label in enumerate(labels)}
    result.update(
        {
            "event_count": row[offset],
            "total_tokens": row[offset + 1],
            "estimated_event_count": row[offset + 2],
            "estimated_cost_usd": numeric_value(row[offset + 3]),
        }
    )
    return result


def sum_known_tokens(*values: int | None) -> int | None:
    known = [value for value in values if value is not None]
    if not known:
        return None
    return sum(known)


def numeric_value(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def token_share(part: int, total: int) -> float | None:
    if total == 0:
        return None
    return round(part / total, 4)
