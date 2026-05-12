from __future__ import annotations

import pytest

from geond.config import Settings
from geond.retrieval.simple import (
    api_rerank_results,
    is_local_rerank_url,
    local_rerank_results,
    normalize_rerank_mode,
    parse_rerank_api_response,
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
    assert search_candidate_limit(10, None, "api") == 30
    assert search_candidate_limit(10, 12, "local") == 12
    assert search_candidate_limit(10, 5, "local") == 10


def test_normalize_rerank_mode_rejects_unknown_mode() -> None:
    assert normalize_rerank_mode(None) is None
    assert normalize_rerank_mode("none") is None
    assert normalize_rerank_mode("local") == "local"
    assert normalize_rerank_mode("api") == "api"
    with pytest.raises(ValueError):
        normalize_rerank_mode("custom")


def test_api_rerank_uses_pluggable_scores(monkeypatch: pytest.MonkeyPatch) -> None:
    results = [
        {"message_id": "msg-1", "snippet": "General notes about another task.", "rank": 0.4},
        {"message_id": "msg-2", "snippet": "Exact app_context fix details.", "rank": 0.1},
    ]

    def fake_call(
        settings: Settings, query: str, candidates: list[dict[str, object]]
    ) -> dict[str, float]:
        assert settings.rerank_url == "http://localhost:8000/rerank"
        assert query == "app_context"
        assert [candidate["id"] for candidate in candidates] == ["msg-1", "msg-2"]
        return {"msg-1": 0.1, "msg-2": 0.95}

    monkeypatch.setattr("geond.retrieval.simple.call_rerank_api", fake_call)

    reranked = api_rerank_results(
        "app_context",
        results,
        limit=2,
        settings=Settings(rerank_url="http://localhost:8000/rerank"),
    )

    assert reranked[0]["message_id"] == "msg-2"
    assert reranked[0]["rerank"] == "api"
    assert reranked[0]["rerank_score"] == 0.95


def test_parse_rerank_api_response_accepts_multiple_shapes() -> None:
    assert parse_rerank_api_response(
        {"results": [{"candidate_id": "msg-1", "relevance": 0.7}]}
    ) == {"msg-1": 0.7}
    assert parse_rerank_api_response(
        {"scores": [{"message_id": "msg-2", "rerank_score": "0.8"}]}
    ) == {"msg-2": 0.8}


def test_local_only_privacy_allows_only_local_rerank_urls() -> None:
    assert is_local_rerank_url("http://localhost:8000/rerank") is True
    assert is_local_rerank_url("http://127.0.0.1:8000/rerank") is True
    assert is_local_rerank_url("https://rerank.example.com") is False
