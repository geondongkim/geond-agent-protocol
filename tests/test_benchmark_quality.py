from __future__ import annotations

import json

from geond.storage.benchmark import (
    benchmark_search,
    evaluate_results,
    format_benchmark_report_markdown,
    load_judgments,
)


class StaticProvider:
    model = "static-test-model"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 1536 for _ in texts]


def test_evaluate_results_scores_expected_targets() -> None:
    results = [
        {
            "message_id": "msg-1",
            "session_external_id": "session-a",
            "source": "codex",
            "workspace_uri": "file:///repo",
            "snippet": "Build answer uses app_context.",
        },
        {
            "message_id": "msg-2",
            "session_external_id": "session-b",
            "source": "claude-code",
            "workspace_uri": "file:///repo",
            "snippet": "Other evidence.",
        },
    ]

    quality = evaluate_results(
        results,
        {
            "expected": [
                {"message_id": "msg-1"},
                {"source": "claude-code", "snippet_contains": "other evidence"},
            ]
        },
        limit=5,
    )

    assert quality["expected_items"] == 2
    assert quality["hits"] == 2
    assert quality["recall_at_k"] == 1.0
    assert quality["first_hit_rank"] == 1
    assert quality["mrr"] == 0.75


def test_load_judgments_supports_query_list(tmp_path) -> None:
    path = tmp_path / "judgments.json"
    path.write_text(
        json.dumps(
            {
                "queries": [
                    {
                        "query": "app_context",
                        "expected_substrings": ["app_context"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    judgments = load_judgments(path)

    assert judgments["app_context"]["expected_substrings"] == ["app_context"]


def test_format_benchmark_report_markdown_includes_quality_columns() -> None:
    markdown = format_benchmark_report_markdown(
        {
            "runs": [
                {
                    "label": "baseline",
                    "mode": "keyword",
                    "provider": "none",
                    "model": "none",
                    "query_count": 1,
                    "total_results": 2,
                    "mean_avg_ms": 1.25,
                    "mean_recall_at_k": 1.0,
                    "mean_mrr": 0.75,
                    "mean_ndcg_at_k": 0.92,
                    "created_at": "2026-05-12T00:00:00",
                }
            ]
        }
    )

    assert "| baseline | keyword | none | none |" in markdown
    assert "nDCG@k" in markdown


def test_benchmark_includes_score_diagnostics(monkeypatch) -> None:
    rows = [
        {
            "source": "seed",
            "session_external_id": "session-a",
            "message_id": "msg-1",
            "rank": 0.25,
            "trigram_score": 0.5,
            "score": 0.75,
            "hybrid_score": 1.0,
            "snippet": "app_context",
        }
    ]

    def run_once(**kwargs):
        return rows

    monkeypatch.setattr("geond.storage.benchmark.run_search_once", run_once)

    result = benchmark_search(
        None,
        ["app_context"],
        mode="hybrid",
        repeat=1,
        provider=StaticProvider(),
        include_results=True,
    )

    top_result = result["queries"][0]["top_results"][0]

    assert top_result["fts_rank"] == 0.25
    assert top_result["trigram_score"] == 0.5
    assert top_result["vector_score"] == 0.75
    assert top_result["hybrid_score"] == 1.0
