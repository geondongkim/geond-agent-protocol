from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from geond.config import get_settings
from geond.db import connect, run_schema_file
from geond.storage.pricing import (
    estimate_usage_cost_usd,
    lookup_model_pricing,
    upsert_model_pricing,
)
from geond.storage.repository import upsert_workspace
from geond.storage.usage import insert_usage_event, summarize_usage

SCHEMA = Path(__file__).parents[1] / "schemas" / "001_initial.sql"
USAGE_SCHEMA = Path(__file__).parents[1] / "schemas" / "003_llm_usage.sql"
PRICING_SCHEMA = Path(__file__).parents[1] / "schemas" / "004_model_pricing.sql"


def test_model_pricing_can_price_usage_events() -> None:
    settings = get_settings()
    workspace_uri = f"file:///tmp/geond-pricing-test-{uuid4()}"

    try:
        conn = connect(settings)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres integration database is not available: {exc}")

    with conn:
        try:
            run_schema_file(conn, SCHEMA)
            run_schema_file(conn, USAGE_SCHEMA)
            run_schema_file(conn, PRICING_SCHEMA)
        except psycopg.Error as exc:
            pytest.skip(f"Postgres integration schema is not available: {exc}")

        workspace_id = upsert_workspace(
            conn,
            root_uri=workspace_uri,
            name="pricing-fixture",
            metadata={"source": "pytest"},
        )
        effective_from = datetime(2020, 1, 1, tzinfo=UTC)
        price_id = upsert_model_pricing(
            conn,
            provider="openai",
            model="gpt-pricing-test",
            input_usd_per_1m_tokens=Decimal("1.00"),
            output_usd_per_1m_tokens=Decimal("2.00"),
            cached_input_usd_per_1m_tokens=Decimal("0.25"),
            reasoning_usd_per_1m_tokens=Decimal("3.00"),
            effective_from=effective_from,
            metadata={"source": "pytest"},
        )
        try:
            price = lookup_model_pricing(
                conn,
                provider="openai",
                model="gpt-pricing-test",
                at=datetime(2026, 1, 1, tzinfo=UTC),
            )
            assert price is not None
            assert price["id"] == price_id
            assert estimate_usage_cost_usd(
                price,
                input_tokens=1000,
                output_tokens=500,
                cached_input_tokens=200,
                reasoning_tokens=10,
            ) == Decimal("0.00208")

            insert_usage_event(
                conn,
                workspace_id=workspace_id,
                source="codex",
                provider="openai",
                model="gpt-pricing-test",
                operation="chat.completion",
                input_tokens=1000,
                output_tokens=500,
                cached_input_tokens=200,
                reasoning_tokens=10,
                estimated=False,
                source_record_id=f"codex:pricing:{uuid4()}",
            )
            summary = summarize_usage(conn, workspace_id)

            assert summary["totals"]["event_count"] == 1
            assert summary["totals"]["total_tokens"] == 1710
            assert summary["totals"]["estimated_cost_usd"] == pytest.approx(0.00208)
            assert summary["data_quality"]["exact_token_share"] == 1.0
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM workspaces WHERE id = %s", (workspace_id,))
                cur.execute("DELETE FROM model_pricing WHERE id = %s", (price_id,))
            conn.commit()
