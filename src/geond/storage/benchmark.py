from __future__ import annotations

from time import perf_counter
from typing import Any

from psycopg import Connection

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
