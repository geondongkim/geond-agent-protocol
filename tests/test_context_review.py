from __future__ import annotations

from geond.storage.context_review import (
    format_context_review_markdown,
    query_tokens,
)


def test_query_tokens_keeps_korean_and_splits_snake_case() -> None:
    tokens = query_tokens("한국어 context_review 개선 src/geond")

    assert "한국어" in tokens
    assert "개선" in tokens
    assert "context_review" in tokens
    assert "context" in tokens
    assert "review" in tokens


def test_format_context_review_markdown_summarizes_loaded_context() -> None:
    markdown = format_context_review_markdown(
        {
            "workspace_uri": "file:///repo",
            "requested": {
                "intent": "Improve review context",
                "file_paths": ["src/geond/storage/context_review.py"],
                "symbols": ["review_workspace_context"],
            },
            "loaded_context": {
                "file_reservations": [{}],
                "symbol_reservations": [],
                "open_handoffs": [{}],
                "lineage_nodes": [{}, {}],
            },
            "matches": [
                {
                    "kind": "changeset",
                    "title": "Add context review",
                    "score": 0.5,
                }
            ],
            "assessment": {
                "status": "advisory_conflicts",
                "reservation_conflict_policy": "advisory",
            },
            "recommendations": ["Review advisory conflicts."],
        }
    )

    assert "# Context Review" in markdown
    assert "file:///repo" in markdown
    assert "Add context review" in markdown
    assert "Review advisory conflicts." in markdown
