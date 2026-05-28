from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from time import perf_counter
from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb

from geond.redaction import redact_text, redact_value

EVIDENCE_SCHEMA = "geond.evidence.v1"


def mcp_audit_enabled() -> bool:
    return os.getenv("GEOND_MCP_AUDIT") == "1"


def mcp_audit_output_enabled() -> bool:
    return os.getenv("GEOND_MCP_AUDIT_OUTPUT") == "1"


def audit_mcp_call(
    conn: Connection,
    *,
    item_name: str,
    input_payload: dict[str, Any],
    callback: Callable[[], Any],
    item_kind: str = "tool",
    client_name: str | None = None,
    workspace_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Any:
    if not mcp_audit_enabled():
        return callback()

    started = perf_counter()
    try:
        output = callback()
    except Exception as exc:
        elapsed_ms = (perf_counter() - started) * 1000
        record_mcp_audit_event(
            conn,
            item_name=item_name,
            input_payload=input_payload,
            item_kind=item_kind,
            client_name=client_name,
            workspace_id=workspace_id,
            elapsed_ms=elapsed_ms,
            status="error",
            error_type=type(exc).__name__,
            error_message=str(exc),
            metadata=metadata,
        )
        raise

    elapsed_ms = (perf_counter() - started) * 1000
    record_mcp_audit_event(
        conn,
        item_name=item_name,
        input_payload=input_payload,
        output_payload=output,
        item_kind=item_kind,
        client_name=client_name,
        workspace_id=workspace_id,
        elapsed_ms=elapsed_ms,
        status="ok",
        metadata=metadata,
    )
    return output


def record_mcp_audit_event(
    conn: Connection,
    *,
    item_name: str,
    input_payload: dict[str, Any],
    output_payload: Any | None = None,
    item_kind: str = "tool",
    client_name: str | None = None,
    workspace_id: str | None = None,
    elapsed_ms: float | None = None,
    status: str = "ok",
    error_type: str | None = None,
    error_message: str | None = None,
    metadata: dict[str, Any] | None = None,
    record_output_body: bool | None = None,
) -> str | None:
    if not mcp_audit_table_exists(conn):
        return None

    redacted_input, _ = redact_value(input_payload)
    redacted_output = None
    should_record_output = (
        record_output_body if record_output_body is not None else mcp_audit_output_enabled()
    )
    if output_payload is not None and should_record_output:
        redacted_output, _ = redact_value(output_payload)
    error_preview = None
    if error_message:
        error_preview, _ = redact_text(error_message)
    output_for_hash = output_payload if output_payload is not None else {"error": error_preview}

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO mcp_audit_events (
                workspace_id,
                item_kind,
                item_name,
                client_name,
                input_redacted,
                output_redacted,
                input_bytes,
                output_bytes,
                input_hash,
                output_hash,
                elapsed_ms,
                status,
                error_type,
                error_message,
                evidence_refs,
                metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id::text
            """,
            (
                workspace_id,
                item_kind,
                item_name,
                client_name,
                Jsonb(redacted_input),
                Jsonb(redacted_output) if redacted_output is not None else None,
                json_byte_size(input_payload),
                json_byte_size(output_for_hash),
                json_hash(input_payload),
                json_hash(output_for_hash),
                elapsed_ms,
                status,
                error_type,
                error_preview,
                Jsonb(extract_evidence_refs(output_payload)),
                Jsonb(metadata or {}),
            ),
        )
        audit_id = cur.fetchone()[0]
    conn.commit()
    return audit_id


def mcp_audit_table_exists(conn: Connection) -> bool:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.mcp_audit_events') IS NOT NULL")
            return bool(cur.fetchone()[0])
    except Exception:
        return False


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")


def json_byte_size(value: Any) -> int:
    return len(json_bytes(value))


def json_hash(value: Any) -> str:
    return hashlib.sha256(json_bytes(value)).hexdigest()


def extract_evidence_refs(value: Any, max_depth: int = 8) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    collect_evidence_refs(value, refs, max_depth=max_depth)
    return refs


def collect_evidence_refs(value: Any, refs: list[dict[str, Any]], max_depth: int) -> None:
    if max_depth <= 0:
        return
    if isinstance(value, dict):
        if value.get("schema") == EVIDENCE_SCHEMA and value.get("kind"):
            refs.append(
                {
                    "kind": value.get("kind"),
                    "target_id": value.get("target_id"),
                    "locator": value.get("locator"),
                }
            )
        for item in value.values():
            collect_evidence_refs(item, refs, max_depth - 1)
    elif isinstance(value, list):
        for item in value:
            collect_evidence_refs(item, refs, max_depth - 1)
