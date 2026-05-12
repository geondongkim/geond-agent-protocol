from __future__ import annotations

import pytest

from geond.retrieval.simple import (
    local_rerank_results,
    normalize_rerank_mode,
    query_tokens,
    search_candidate_limit,
)


def test_local_rerank_promotes_exact_query_match() -> None:
    results = [
        {
            "message_id": "msg-1",
            "snippet": "General notes about another task.",
            "rank": 0.4,
            "trigram_score": 0.1,
        },
        {
            "message_id": "msg-2",
            "snippet": "The app_context failure was fixed in the service layer.",
            "rank": 0.1,
            "trigram_score": 0.2,
        },
    ]

    reranked = local_rerank_results("app_context", results, limit=2)

    assert reranked[0]["message_id"] == "msg-2"
    assert reranked[0]["rerank"] == "local"
    assert reranked[0]["rerank_base_rank"] == 2
    assert reranked[0]["rerank_score"] > reranked[1]["rerank_score"]


def test_query_tokens_supports_korean_and_code_terms() -> None:
    assert query_tokens("왜 app_context 파일이 바뀌었어?") == [
        "왜",
        "app_context",
        "파일이",
        "바뀌었어",
    ]


def test_search_candidate_limit_only_expands_for_rerank() -> None:
    assert search_candidate_limit(10, None, None) == 10
    assert search_candidate_limit(10, None, "local") == 30
    assert search_candidate_limit(10, 12, "local") == 12
    assert search_candidate_limit(10, 5, "local") == 10


def test_normalize_rerank_mode_rejects_unknown_mode() -> None:
    assert normalize_rerank_mode(None) is None
    assert normalize_rerank_mode("none") is None
    assert normalize_rerank_mode("local") == "local"
    with pytest.raises(ValueError):
        normalize_rerank_mode("api")
