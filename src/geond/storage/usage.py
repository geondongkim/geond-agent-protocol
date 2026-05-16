from __future__ import annotations

from decimal import Decimal
from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb

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
                estimated_cost_usd,
                priced_at,
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

    return {
        "status": "ok",
        "workspace_id": workspace_id,
        "filters": {"source": source, "provider": provider, "model": model},
        "totals": totals,
        "by_source": by_source,
        "by_model": by_model,
        "data_quality": {
            "exact_event_count": totals["exact_event_count"],
            "estimated_event_count": totals["estimated_event_count"],
            "exact_token_share": token_share(totals["exact_tokens"], totals["total_tokens"]),
            "estimated_token_share": token_share(
                totals["estimated_tokens"], totals["total_tokens"]
            ),
        },
    }


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
