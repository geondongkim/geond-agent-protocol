from __future__ import annotations

from pathlib import Path

from geond.adapters.codex import parse_session_file, parse_storage, to_summary

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "codex"
FIXTURE_SESSION = (
    FIXTURE_ROOT
    / "sessions"
    / "2026"
    / "05"
    / "11"
    / "rollout-2026-05-11T15-42-45-codex-session-1.jsonl"
)


def test_parse_codex_session_file_extracts_metadata_events_and_messages() -> None:
    session = parse_session_file(
        FIXTURE_SESSION,
        title_index={"codex-session-1": "Codex parser fixture"},
    )

    assert session.session_id == "codex-session-1"
    assert session.title == "Codex parser fixture"
    assert session.metadata["cwd"].endswith("RealMe_OPIc")
    assert session.metadata["originator"] == "codex_vscode"
    assert session.metadata["model_provider"] == "openai"
    assert len(session.events) == 5
    assert [message.role for message in session.messages] == ["user", "assistant", "assistant"]
    assert "추가 테스트베드" in session.messages[0].content
    assert "세션 파서" in session.messages[-1].content


def test_parse_codex_storage_uses_session_index_and_limit() -> None:
    sessions = parse_storage(FIXTURE_ROOT, limit=1)

    assert len(sessions) == 1
    assert sessions[0].title == "Codex parser fixture"
    assert to_summary(sessions[0])["message_count"] == 3

