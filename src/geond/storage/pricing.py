from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb

MILLION = Decimal("1000000")


def upsert_model_pricing(
    conn: Connection,
    *,
    provider: str,
    model: str,
    input_usd_per_1m_tokens: Decimal | float | str | None = None,
    output_usd_per_1m_tokens: Decimal | float | str | None = None,
    cached_input_usd_per_1m_tokens: Decimal | float | str | None = None,
    reasoning_usd_per_1m_tokens: Decimal | float | str | None = None,
    effective_from: datetime | None = None,
    effective_to: datetime | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    resolved_effective_from = effective_from or datetime.now(UTC)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO model_pricing (
                provider,
                model,
                input_usd_per_1m_tokens,
                output_usd_per_1m_tokens,
                cached_input_usd_per_1m_tokens,
                reasoning_usd_per_1m_tokens,
                effective_from,
                effective_to,
                metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (provider, model, effective_from) DO UPDATE SET
                input_usd_per_1m_tokens = EXCLUDED.input_usd_per_1m_tokens,
                output_usd_per_1m_tokens = EXCLUDED.output_usd_per_1m_tokens,
                cached_input_usd_per_1m_tokens = EXCLUDED.cached_input_usd_per_1m_tokens,
                reasoning_usd_per_1m_tokens = EXCLUDED.reasoning_usd_per_1m_tokens,
                effective_to = EXCLUDED.effective_to,
                metadata = EXCLUDED.metadata
            RETURNING id::text
            """,
            (
                provider,
                model,
                decimal_or_none(input_usd_per_1m_tokens),
                decimal_or_none(output_usd_per_1m_tokens),
                decimal_or_none(cached_input_usd_per_1m_tokens),
                decimal_or_none(reasoning_usd_per_1m_tokens),
                resolved_effective_from,
                effective_to,
                Jsonb(metadata or {}),
            ),
        )
        price_id = cur.fetchone()[0]
    conn.commit()
    return price_id


def lookup_model_pricing(
    conn: Connection,
    *,
    provider: str | None,
    model: str | None,
    at: datetime | None = None,
) -> dict[str, Any] | None:
    if not provider or not model or not model_pricing_table_exists(conn):
        return None
    effective_at = at or datetime.now(UTC)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                id::text,
                provider,
                model,
                input_usd_per_1m_tokens,
                output_usd_per_1m_tokens,
                cached_input_usd_per_1m_tokens,
                reasoning_usd_per_1m_tokens,
                effective_from,
                effective_to,
                metadata
            FROM model_pricing
            WHERE provider = %s
              AND model = %s
              AND effective_from <= %s
              AND (effective_to IS NULL OR effective_to > %s)
            ORDER BY effective_from DESC
            LIMIT 1
            """,
            (provider, model, effective_at, effective_at),
        )
        row = cur.fetchone()
    if not row:
        return None
    return pricing_row(row)


def estimate_usage_cost_usd(
    price: dict[str, Any] | None,
    *,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cached_input_tokens: int | None = None,
    reasoning_tokens: int | None = None,
) -> Decimal | None:
    if not price:
        return None
    parts = [
        token_cost(input_tokens, price.get("input_usd_per_1m_tokens")),
        token_cost(output_tokens, price.get("output_usd_per_1m_tokens")),
        token_cost(cached_input_tokens, price.get("cached_input_usd_per_1m_tokens")),
        token_cost(reasoning_tokens, price.get("reasoning_usd_per_1m_tokens")),
    ]
    known_parts = [part for part in parts if part is not None]
    if not known_parts:
        return None
    return sum(known_parts, Decimal("0"))


def model_pricing_table_exists(conn: Connection) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.model_pricing') IS NOT NULL")
        return bool(cur.fetchone()[0])


def pricing_row(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "id": row[0],
        "provider": row[1],
        "model": row[2],
        "input_usd_per_1m_tokens": decimal_or_none(row[3]),
        "output_usd_per_1m_tokens": decimal_or_none(row[4]),
        "cached_input_usd_per_1m_tokens": decimal_or_none(row[5]),
        "reasoning_usd_per_1m_tokens": decimal_or_none(row[6]),
        "effective_from": row[7],
        "effective_to": row[8],
        "metadata": row[9] or {},
    }


def token_cost(tokens: int | None, rate_per_1m: Decimal | None) -> Decimal | None:
    if tokens is None or rate_per_1m is None:
        return None
    return (Decimal(tokens) * rate_per_1m) / MILLION


def decimal_or_none(value: Decimal | float | str | int | None) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))
