from __future__ import annotations

import os
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


def test_parse_codex_storage_limits_to_newest_session(tmp_path: Path) -> None:
    older = tmp_path / "sessions" / "2026" / "05" / "01" / "older.jsonl"
    newer = tmp_path / "sessions" / "2026" / "05" / "02" / "newer.jsonl"
    for path, session_id in ((older, "older"), (newer, "newer")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f'{{"type":"session_meta","payload":{{"id":"{session_id}"}}}}\n',
            encoding="utf-8",
        )
    os.utime(older, (1000, 1000))
    os.utime(newer, (2000, 2000))

    sessions = parse_storage(tmp_path, limit=1)

    assert len(sessions) == 1
    assert sessions[0].session_id == "newer"


def test_parse_codex_session_skips_malformed_jsonl_lines(tmp_path: Path) -> None:
    session_file = tmp_path / "codex-bad-line.jsonl"
    session_file.write_text(
        "\n".join(
            [
                '{"type":"session_meta","payload":{"id":"codex-good-session","cwd":"/repo"}}',
                '{"type":"response_item","payload":{"type":"message","role":"user",'
                '"content":[{"type":"input_text","text":"before bad line"}]}}',
                '{"type":"response_item","payload":',
                '{"type":"response_item","payload":{"type":"message","role":"assistant",'
                '"content":[{"type":"output_text","text":"after bad line"}]}}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    session = parse_session_file(session_file)

    assert session.session_id == "codex-good-session"
    assert [message.content for message in session.messages] == [
        "before bad line",
        "after bad line",
    ]
