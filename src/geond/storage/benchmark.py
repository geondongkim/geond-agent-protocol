from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from time import perf_counter
from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb

from geond.embeddings import EmbeddingProvider
from geond.redaction import redact_text
from geond.retrieval.simple import (
    hybrid_search_dev_memory,
    search_dev_memory,
    vector_search_dev_memory,
)
from geond.storage.repository import resolve_workspace_id

AGENT_RUN_SCHEMA = "geond.agent_run_benchmark.v1"


def benchmark_search(
    conn: Connection,
    queries: list[str],
    mode: str = "keyword",
    repeat: int = 3,
    limit: int = 10,
    workspace_uri: str | None = None,
    source: str | None = None,
    provider: EmbeddingProvider | None = None,
    judgments: dict[str, dict[str, Any]] | None = None,
    include_results: bool = False,
    rerank: str = "none",
    candidate_limit: int | None = None,
) -> dict[str, Any]:
    if repeat < 1:
        raise ValueError("repeat must be at least 1")
    if mode in {"vector", "hybrid"} and provider is None:
        raise ValueError("provider is required for vector or hybrid benchmark modes")

    query_results: list[dict[str, Any]] = []
    for query in queries:
        durations_ms: list[float] = []
        result_count = 0
        rows: list[dict[str, Any]] = []
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
                rerank=rerank,
                candidate_limit=candidate_limit,
            )
            durations_ms.append((perf_counter() - started) * 1000)
            result_count = len(rows)
        query_result: dict[str, Any] = {
            "query": query,
            "result_count": result_count,
            "min_ms": round(min(durations_ms), 3),
            "avg_ms": round(sum(durations_ms) / len(durations_ms), 3),
            "max_ms": round(max(durations_ms), 3),
        }
        if judgments and query in judgments:
            query_result["quality"] = evaluate_results(rows, judgments[query], limit)
        if rerank != "none":
            query_result["rerank_diagnostics"] = evaluate_rerank_diagnostics(rows, limit)
        if include_results:
            query_result["top_results"] = [
                {
                    "rank": rank,
                    "source": row.get("source"),
                    "session_external_id": row.get("session_external_id"),
                    "message_id": row.get("message_id"),
                    "fts_rank": row.get("rank"),
                    "trigram_score": row.get("trigram_score"),
                    "vector_score": row.get("score"),
                    "hybrid_score": row.get("hybrid_score"),
                    "rerank": row.get("rerank"),
                    "rerank_base_rank": row.get("rerank_base_rank"),
                    "rerank_base_score": row.get("rerank_base_score"),
                    "rerank_score": row.get("rerank_score"),
                    "rerank_missing_score": row.get("rerank_missing_score"),
                    "rerank_total_score": row.get("rerank_total_score"),
                    "snippet": row.get("snippet"),
                }
                for rank, row in enumerate(rows[:limit], start=1)
            ]
        query_results.append(query_result)

    result = {
        "mode": mode,
        "rerank": rerank,
        "candidate_limit": candidate_limit,
        "repeat": repeat,
        "limit": limit,
        "queries": query_results,
    }
    if rerank != "none":
        result["rerank_summary"] = summarize_rerank_diagnostics(query_results)
    return result


def save_benchmark_run(
    conn: Connection,
    result: dict[str, Any],
    label: str = "",
    workspace_uri: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    metadata: dict[str, Any] | None = None,
    kind: str = "search",
) -> str:
    workspace_id = resolve_workspace_id(conn, workspace_uri) if workspace_uri else None
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO benchmark_runs (
                workspace_id,
                kind,
                label,
                mode,
                provider,
                model,
                repeat,
                result,
                metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id::text
            """,
            (
                workspace_id,
                kind,
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


def save_agent_run_benchmark(
    conn: Connection,
    *,
    agent: str,
    command: str | list[str],
    workspace_uri: str | None = None,
    label: str = "",
    prompt_text: str | None = None,
    prompt_hash: str | None = None,
    prompt_label: str | None = None,
    wall_time_ms: float | None = None,
    provider: str | None = None,
    model: str | None = None,
    sandbox: str | None = None,
    approval: str | None = None,
    final_output: str | None = None,
    final_output_hash: str | None = None,
    stdout_path: str | None = None,
    stderr_path: str | None = None,
    transcript_paths: list[str] | None = None,
    log_paths: list[str] | None = None,
    stdout_bytes: int | None = None,
    stderr_bytes: int | None = None,
    token_usage: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    normalized_command = command if isinstance(command, str) else " ".join(command)
    resolved_prompt_hash = prompt_hash or sha256_text(prompt_text)
    resolved_final_hash = final_output_hash or sha256_text(final_output)
    final_excerpt = None
    if final_output:
        redacted_output, _ = redact_text(final_output)
        final_excerpt = redacted_output[:240]
    resolved_stdout_bytes = stdout_bytes if stdout_bytes is not None else path_size(stdout_path)
    resolved_stderr_bytes = stderr_bytes if stderr_bytes is not None else path_size(stderr_path)
    result = {
        "schema": AGENT_RUN_SCHEMA,
        "mode": agent,
        "agent": agent,
        "command": normalized_command,
        "prompt_hash": resolved_prompt_hash,
        "prompt_label": prompt_label,
        "wall_time_ms": wall_time_ms,
        "provider": provider,
        "model": model,
        "sandbox": sandbox,
        "approval": approval,
        "final_output_hash": resolved_final_hash,
        "final_output_excerpt": final_excerpt,
        "stdout": {"path": stdout_path, "bytes": resolved_stdout_bytes},
        "stderr": {"path": stderr_path, "bytes": resolved_stderr_bytes},
        "transcript_paths": transcript_paths or [],
        "log_paths": log_paths or [],
        "token_usage": token_usage or {},
        "repeat": 1,
    }
    return save_benchmark_run(
        conn,
        result,
        label=label,
        workspace_uri=workspace_uri,
        provider=provider,
        model=model,
        metadata=metadata or {},
        kind="agent-run",
    )


def list_benchmark_runs(
    conn: Connection,
    workspace_uri: str | None = None,
    mode: str | None = None,
    kind: str | None = "search",
    limit: int = 20,
) -> list[dict[str, Any]]:
    filters: list[str] = []
    params: list[Any] = []
    if workspace_uri:
        workspace_id = resolve_workspace_id(conn, workspace_uri)
        if not workspace_id:
            return []
        filters.append("br.workspace_id = %s::uuid")
        params.append(workspace_id)
    if mode:
        filters.append("br.mode = %s")
        params.append(mode)
    if kind and kind != "all":
        filters.append("br.kind = %s")
        params.append(kind)
    where_clause = "WHERE " + " AND ".join(filters) if filters else ""
    params.append(limit)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT
                br.id::text,
                w.root_uri,
                br.kind,
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
            "kind": row[2],
            "label": row[3],
            "mode": row[4],
            "provider": row[5],
            "model": row[6],
            "repeat": row[7],
            "result": row[8],
            "created_at": row[9].isoformat() if row[9] else None,
        }
        for row in rows
    ]


def compare_benchmark_runs(
    conn: Connection,
    workspace_uri: str | None = None,
    mode: str | None = None,
    kind: str | None = "search",
    limit: int = 20,
) -> dict[str, Any]:
    runs = list_benchmark_runs(
        conn,
        workspace_uri=workspace_uri,
        mode=mode,
        kind=kind,
        limit=limit,
    )
    rows: list[dict[str, Any]] = []
    for run in runs:
        query_rows = run["result"].get("queries", [])
        avg_ms_values = [item["avg_ms"] for item in query_rows if "avg_ms" in item]
        recall_values = metric_values(query_rows, "recall_at_k")
        mrr_values = metric_values(query_rows, "mrr")
        ndcg_values = metric_values(query_rows, "ndcg_at_k")
        rerank_score_values = diagnostic_values(query_rows, "mean_rerank_score")
        rank_delta_values = diagnostic_values(query_rows, "mean_abs_rank_delta")
        total_results = sum(item.get("result_count", 0) for item in query_rows)
        top_changed = sum(
            1
            for item in query_rows
            if (item.get("rerank_diagnostics") or {}).get("top_result_changed") is True
        )
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
                "mean_recall_at_k": rounded_mean(recall_values),
                "mean_mrr": rounded_mean(mrr_values),
                "mean_ndcg_at_k": rounded_mean(ndcg_values),
                "rerank_top_changed_queries": top_changed,
                "mean_rerank_score": rounded_mean(rerank_score_values),
                "mean_abs_rank_delta": rounded_mean(rank_delta_values),
                "created_at": run["created_at"],
            }
        )
    return {"runs": rows}


def compare_agent_run_benchmark_runs(
    conn: Connection,
    workspace_uri: str | None = None,
    agent: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    runs = list_benchmark_runs(
        conn,
        workspace_uri=workspace_uri,
        mode=agent,
        kind="agent-run",
        limit=limit,
    )
    rows = []
    for run in runs:
        result = run.get("result") or {}
        stdout = result.get("stdout") if isinstance(result.get("stdout"), dict) else {}
        stderr = result.get("stderr") if isinstance(result.get("stderr"), dict) else {}
        token_usage = (
            result.get("token_usage") if isinstance(result.get("token_usage"), dict) else {}
        )
        rows.append(
            {
                "benchmark_run_id": run.get("benchmark_run_id"),
                "label": run.get("label"),
                "workspace_uri": run.get("workspace_uri"),
                "agent": result.get("agent") or run.get("mode"),
                "provider": run.get("provider"),
                "model": run.get("model"),
                "wall_time_ms": result.get("wall_time_ms"),
                "stdout_bytes": stdout.get("bytes"),
                "stderr_bytes": stderr.get("bytes"),
                "transcript_path": first_path(result.get("transcript_paths")),
                "prompt_label": result.get("prompt_label"),
                "total_tokens": token_usage.get("total_tokens"),
                "created_at": run.get("created_at"),
            }
        )
    return {"runs": rows}


def load_judgments(path: Path) -> dict[str, dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "queries" in data:
        data = data["queries"]
    if isinstance(data, list):
        return {
            item["query"]: item
            for item in data
            if isinstance(item, dict) and isinstance(item.get("query"), str)
        }
    if isinstance(data, dict):
        return {
            str(query): judgment for query, judgment in data.items() if isinstance(judgment, dict)
        }
    raise ValueError("judgments must be a JSON object or list of query judgment objects")


def evaluate_results(
    results: list[dict[str, Any]],
    judgment: dict[str, Any],
    limit: int,
) -> dict[str, Any]:
    targets = expected_targets(judgment)
    if not targets:
        return {
            "expected_items": 0,
            "hits": 0,
            "recall_at_k": None,
            "mrr": None,
            "ndcg_at_k": None,
            "first_hit_rank": None,
        }

    ranks: list[int] = []
    for target in targets:
        rank = first_matching_rank(results[:limit], target)
        if rank is not None:
            ranks.append(rank)

    hits = len(ranks)
    ideal_hits = min(len(targets), limit)
    dcg = sum(1.0 / math.log2(rank + 1) for rank in ranks)
    ideal_dcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return {
        "expected_items": len(targets),
        "hits": hits,
        "recall_at_k": round(hits / len(targets), 4),
        "mrr": round(sum(1.0 / rank for rank in ranks) / len(targets), 4),
        "ndcg_at_k": round(dcg / ideal_dcg, 4) if ideal_dcg else None,
        "first_hit_rank": min(ranks) if ranks else None,
    }


def expected_targets(judgment: dict[str, Any]) -> list[dict[str, Any]]:
    explicit = judgment.get("expected")
    if isinstance(explicit, list):
        return [item for item in explicit if isinstance(item, dict)]

    targets: list[dict[str, Any]] = []
    field_aliases = {
        "expected_message_ids": "message_id",
        "expected_session_ids": "session_external_id",
        "expected_sources": "source",
        "expected_workspace_uris": "workspace_uri",
        "expected_substrings": "snippet_contains",
    }
    for source_key, target_key in field_aliases.items():
        values = judgment.get(source_key)
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            continue
        targets.extend({target_key: value} for value in values if isinstance(value, str))
    return targets


def first_matching_rank(
    results: list[dict[str, Any]],
    target: dict[str, Any],
) -> int | None:
    for rank, result in enumerate(results, start=1):
        if result_matches(result, target):
            return rank
    return None


def result_matches(result: dict[str, Any], target: dict[str, Any]) -> bool:
    for key in ("message_id", "session_external_id", "source", "workspace_uri"):
        expected = target.get(key)
        if expected is not None and result.get(key) != expected:
            return False
    snippet_contains = target.get("snippet_contains")
    if snippet_contains is not None:
        snippet = str(result.get("snippet") or "").lower()
        if str(snippet_contains).lower() not in snippet:
            return False
    return True


def metric_values(query_rows: list[dict[str, Any]], metric: str) -> list[float]:
    values: list[float] = []
    for item in query_rows:
        quality = item.get("quality")
        if not isinstance(quality, dict):
            continue
        value = quality.get(metric)
        if isinstance(value, int | float):
            values.append(float(value))
    return values


def diagnostic_values(query_rows: list[dict[str, Any]], metric: str) -> list[float]:
    values: list[float] = []
    for item in query_rows:
        diagnostics = item.get("rerank_diagnostics")
        if not isinstance(diagnostics, dict):
            continue
        value = diagnostics.get(metric)
        if isinstance(value, int | float):
            values.append(float(value))
    return values


def rounded_mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def evaluate_rerank_diagnostics(results: list[dict[str, Any]], limit: int) -> dict[str, Any]:
    rows = results[:limit]
    reranked = [row for row in rows if row.get("rerank")]
    if not reranked:
        return {
            "enabled": False,
            "reranked_results": 0,
            "top_result_changed": False,
            "promoted_results": 0,
            "demoted_results": 0,
            "mean_abs_rank_delta": None,
            "mean_rerank_score": None,
            "missing_score_count": 0,
        }

    deltas: list[int] = []
    scores: list[float] = []
    missing_score_count = 0
    for current_rank, row in enumerate(reranked, start=1):
        base_rank = int(row.get("rerank_base_rank") or current_rank)
        deltas.append(base_rank - current_rank)
        score = row.get("rerank_score")
        if isinstance(score, int | float):
            scores.append(float(score))
        if row.get("rerank_missing_score") is True:
            missing_score_count += 1
    return {
        "enabled": True,
        "reranked_results": len(reranked),
        "top_result_changed": bool(reranked and reranked[0].get("rerank_base_rank") != 1),
        "promoted_results": sum(1 for delta in deltas if delta > 0),
        "demoted_results": sum(1 for delta in deltas if delta < 0),
        "mean_abs_rank_delta": round(sum(abs(delta) for delta in deltas) / len(deltas), 4),
        "mean_rerank_score": rounded_mean(scores),
        "missing_score_count": missing_score_count,
    }


def summarize_rerank_diagnostics(query_rows: list[dict[str, Any]]) -> dict[str, Any]:
    diagnostics = [
        item["rerank_diagnostics"]
        for item in query_rows
        if isinstance(item.get("rerank_diagnostics"), dict)
    ]
    if not diagnostics:
        return {"query_count": 0}
    changed = sum(1 for item in diagnostics if item.get("top_result_changed") is True)
    return {
        "query_count": len(diagnostics),
        "top_result_changed_queries": changed,
        "mean_reranked_results": rounded_mean(
            [float(item["reranked_results"]) for item in diagnostics if item.get("enabled")]
        ),
        "mean_abs_rank_delta": rounded_mean(
            diagnostic_number_values(diagnostics, "mean_abs_rank_delta")
        ),
        "mean_rerank_score": rounded_mean(
            diagnostic_number_values(diagnostics, "mean_rerank_score")
        ),
        "missing_score_count": sum(
            int(item.get("missing_score_count") or 0) for item in diagnostics
        ),
    }


def diagnostic_number_values(diagnostics: list[dict[str, Any]], metric: str) -> list[float]:
    return [
        float(item[metric]) for item in diagnostics if isinstance(item.get(metric), int | float)
    ]


def format_benchmark_report_markdown(report: dict[str, Any]) -> str:
    rows = report.get("runs", [])
    lines = [
        "# Benchmark Report",
        "",
        "| Label | Mode | Provider | Model | Queries | Results | Mean avg ms | "
        "Recall@k | MRR | nDCG@k | Rerank top changed | Mean rerank score | "
        "Mean rank delta | Created |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | "
        "---: | ---: | ---: | --- |",
    ]
    for row in rows:
        row_template = (
            "| {label} | {mode} | {provider} | {model} | {query_count} | {total_results} | "
            "{mean_avg_ms} | {mean_recall_at_k} | {mean_mrr} | {mean_ndcg_at_k} | "
            "{rerank_top_changed_queries} | {mean_rerank_score} | {mean_abs_rank_delta} | "
            "{created_at} |"
        )
        lines.append(
            row_template.format(
                label=row.get("label") or "",
                mode=row.get("mode") or "",
                provider=row.get("provider") or "",
                model=row.get("model") or "",
                query_count=row.get("query_count") or 0,
                total_results=row.get("total_results") or 0,
                mean_avg_ms=markdown_value(row.get("mean_avg_ms")),
                mean_recall_at_k=markdown_value(row.get("mean_recall_at_k")),
                mean_mrr=markdown_value(row.get("mean_mrr")),
                mean_ndcg_at_k=markdown_value(row.get("mean_ndcg_at_k")),
                rerank_top_changed_queries=markdown_value(row.get("rerank_top_changed_queries")),
                mean_rerank_score=markdown_value(row.get("mean_rerank_score")),
                mean_abs_rank_delta=markdown_value(row.get("mean_abs_rank_delta")),
                created_at=row.get("created_at") or "",
            )
        )
    return "\n".join(lines)


def format_agent_run_report_markdown(report: dict[str, Any]) -> str:
    rows = report.get("runs", [])
    lines = [
        "# Agent Run Benchmark Report",
        "",
        "| Label | Agent | Provider | Model | Wall ms | Stdout bytes | "
        "Transcript | Prompt | Tokens | Created |",
        "| --- | --- | --- | --- | ---: | ---: | --- | --- | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            "| {label} | {agent} | {provider} | {model} | {wall_time_ms} | "
            "{stdout_bytes} | {transcript_path} | {prompt_label} | {total_tokens} | "
            "{created_at} |".format(
                label=row.get("label") or "",
                agent=row.get("agent") or "",
                provider=row.get("provider") or "",
                model=row.get("model") or "",
                wall_time_ms=markdown_value(row.get("wall_time_ms")),
                stdout_bytes=markdown_value(row.get("stdout_bytes")),
                transcript_path=row.get("transcript_path") or "",
                prompt_label=row.get("prompt_label") or "",
                total_tokens=markdown_value(row.get("total_tokens")),
                created_at=row.get("created_at") or "",
            )
        )
    return "\n".join(lines)


def markdown_value(value: Any) -> str:
    return "" if value is None else str(value)


def sha256_text(value: str | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def path_size(path: str | None) -> int | None:
    if not path:
        return None
    candidate = Path(path)
    if not candidate.exists() or not candidate.is_file():
        return None
    return candidate.stat().st_size


def first_path(paths: Any) -> str | None:
    if isinstance(paths, list) and paths:
        first = paths[0]
        return first if isinstance(first, str) else str(first)
    return None


def run_search_once(
    conn: Connection,
    query: str,
    mode: str,
    limit: int,
    workspace_uri: str | None,
    source: str | None,
    provider: EmbeddingProvider | None,
    rerank: str = "none",
    candidate_limit: int | None = None,
) -> list[dict[str, Any]]:
    if mode == "keyword":
        return search_dev_memory(
            conn,
            query,
            limit=limit,
            workspace_uri=workspace_uri,
            source=source,
            rerank=rerank,
            candidate_limit=candidate_limit,
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
            query=query,
            rerank=rerank,
            candidate_limit=candidate_limit,
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
            rerank=rerank,
            candidate_limit=candidate_limit,
        )
    raise ValueError("mode must be one of: keyword, vector, hybrid")
