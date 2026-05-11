from __future__ import annotations

from time import perf_counter
from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb

from geond.embeddings import EmbeddingProvider
from geond.retrieval.simple import (
    hybrid_search_dev_memory,
    search_dev_memory,
    vector_search_dev_memory,
)


def benchmark_search(
    conn: Connection,
    queries: list[str],
    mode: str = "keyword",
    repeat: int = 3,
    limit: int = 10,
    workspace_uri: str | None = None,
    source: str | None = None,
    provider: EmbeddingProvider | None = None,
) -> dict[str, Any]:
    if repeat < 1:
        raise ValueError("repeat must be at least 1")
    if mode in {"vector", "hybrid"} and provider is None:
        raise ValueError("provider is required for vector or hybrid benchmark modes")

    query_results: list[dict[str, Any]] = []
    for query in queries:
        durations_ms: list[float] = []
        result_count = 0
        for _ in range(repeat):
            started = perf_counter()
            rows = run_search_once(
                conn=conn,
                query=query,
                mode=mode,
                limit=limit,
                workspace_uri=workspace_uri,
                source=source,
                provider=provider,
            )
            durations_ms.append((perf_counter() - started) * 1000)
            result_count = len(rows)
        query_results.append(
            {
                "query": query,
                "result_count": result_count,
                "min_ms": round(min(durations_ms), 3),
                "avg_ms": round(sum(durations_ms) / len(durations_ms), 3),
                "max_ms": round(max(durations_ms), 3),
            }
        )

    return {
        "mode": mode,
        "repeat": repeat,
        "limit": limit,
        "queries": query_results,
    }


def save_benchmark_run(
    conn: Connection,
    result: dict[str, Any],
    label: str = "",
    workspace_uri: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    workspace_id = resolve_workspace_id(conn, workspace_uri) if workspace_uri else None
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO benchmark_runs (
                workspace_id,
                label,
                mode,
                provider,
                model,
                repeat,
                result,
                metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id::text
            """,
            (
                workspace_id,
                label,
                result["mode"],
                provider,
                model,
                result["repeat"],
                Jsonb(result),
                Jsonb(metadata or {}),
            ),
        )
        run_id = cur.fetchone()[0]
    conn.commit()
    return run_id


def list_benchmark_runs(
    conn: Connection,
    workspace_uri: str | None = None,
    mode: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    filters: list[str] = []
    params: list[Any] = []
    if workspace_uri:
        filters.append("w.root_uri = %s")
        params.append(workspace_uri)
    if mode:
        filters.append("br.mode = %s")
        params.append(mode)
    where_clause = "WHERE " + " AND ".join(filters) if filters else ""
    params.append(limit)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT
                br.id::text,
                w.root_uri,
                br.label,
                br.mode,
                br.provider,
                br.model,
                br.repeat,
                br.result,
                br.created_at
            FROM benchmark_runs br
            LEFT JOIN workspaces w ON w.id = br.workspace_id
            {where_clause}
            ORDER BY br.created_at DESC
            LIMIT %s
            """,
            params,
        )
        rows = cur.fetchall()
    return [
        {
            "benchmark_run_id": row[0],
            "workspace_uri": row[1],
            "label": row[2],
            "mode": row[3],
            "provider": row[4],
            "model": row[5],
            "repeat": row[6],
            "result": row[7],
            "created_at": row[8].isoformat() if row[8] else None,
        }
        for row in rows
    ]


def compare_benchmark_runs(
    conn: Connection,
    workspace_uri: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    runs = list_benchmark_runs(conn, workspace_uri=workspace_uri, limit=limit)
    rows: list[dict[str, Any]] = []
    for run in runs:
        query_rows = run["result"].get("queries", [])
        avg_ms_values = [item["avg_ms"] for item in query_rows if "avg_ms" in item]
        total_results = sum(item.get("result_count", 0) for item in query_rows)
        rows.append(
            {
                "benchmark_run_id": run["benchmark_run_id"],
                "label": run["label"],
                "workspace_uri": run["workspace_uri"],
                "mode": run["mode"],
                "provider": run["provider"],
                "model": run["model"],
                "query_count": len(query_rows),
                "total_results": total_results,
                "mean_avg_ms": round(sum(avg_ms_values) / len(avg_ms_values), 3)
                if avg_ms_values
                else None,
                "created_at": run["created_at"],
            }
        )
    return {"runs": rows}


def resolve_workspace_id(conn: Connection, workspace_uri: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT id::text FROM workspaces WHERE root_uri = %s", (workspace_uri,))
        row = cur.fetchone()
    return row[0] if row else None


def run_search_once(
    conn: Connection,
    query: str,
    mode: str,
    limit: int,
    workspace_uri: str | None,
    source: str | None,
    provider: EmbeddingProvider | None,
) -> list[dict[str, Any]]:
    if mode == "keyword":
        return search_dev_memory(
            conn,
            query,
            limit=limit,
            workspace_uri=workspace_uri,
            source=source,
        )
    if provider is None:
        raise ValueError("provider is required for vector or hybrid search")

    query_vector = provider.embed([query])[0]
    if mode == "vector":
        return vector_search_dev_memory(
            conn,
            query_vector=query_vector,
            model=provider.model,
            limit=limit,
            workspace_uri=workspace_uri,
            source=source,
        )
    if mode == "hybrid":
        return hybrid_search_dev_memory(
            conn,
            query=query,
            query_vector=query_vector,
            model=provider.model,
            limit=limit,
            workspace_uri=workspace_uri,
            source=source,
        )
    raise ValueError("mode must be one of: keyword, vector, hybrid")
