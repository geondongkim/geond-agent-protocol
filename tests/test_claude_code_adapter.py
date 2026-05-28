from __future__ import annotations

import os
from pathlib import Path

from geond.adapters.claude_code import (
    decode_project_path,
    parse_session_file,
    parse_storage,
    to_summary,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "claude_code"
FIXTURE_SESSION = FIXTURE_ROOT / "projects" / "c--test-project" / "test-session-1.jsonl"


def test_parse_session_extracts_metadata() -> None:
    session = parse_session_file(FIXTURE_SESSION)

    assert session.session_id == "test-session-1"
    assert session.metadata["cwd"] == "/test/project"
    assert session.metadata["gitBranch"] == "main"
    assert session.metadata["version"] == "2.1.112"


def test_parse_session_messages_skip_thinking_and_tool_only_blocks() -> None:
    session = parse_session_file(FIXTURE_SESSION)

    # user + one assistant (text only; thinking-only and tool-only blocks excluded)
    assert [m.role for m in session.messages] == ["user", "assistant"]
    assert "Claude Code importer fixture" in session.messages[0].content
    assert "parser works" in session.messages[1].content


def test_parse_session_events_include_all_records() -> None:
    session = parse_session_file(FIXTURE_SESSION)

    assert len(session.events) == 6
    types = [e.record_type for e in session.events]
    assert types.count("queue-operation") == 2
    assert types.count("user") == 1
    assert types.count("assistant") == 3


def test_parse_session_captures_tool_call() -> None:
    session = parse_session_file(FIXTURE_SESSION)

    tool_events = [e for e in session.events if e.tool_name is not None]
    assert len(tool_events) == 1
    assert tool_events[0].tool_name == "Read"
    assert tool_events[0].tool_input == {"file_path": "/test/project/README.md"}


def test_parse_storage_with_limit() -> None:
    sessions = parse_storage(FIXTURE_ROOT, limit=1)

    assert len(sessions) == 1
    summary = to_summary(sessions[0])
    assert summary["session_id"] == "test-session-1"
    assert summary["message_count"] == 2
    assert summary["git_branch"] == "main"


def test_parse_storage_with_limit_uses_newest_session(tmp_path: Path) -> None:
    older = tmp_path / "projects" / "c--test-project" / "older.jsonl"
    newer = tmp_path / "projects" / "c--test-project" / "newer.jsonl"
    for path, session_id in ((older, "older"), (newer, "newer")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            (
                '{"type":"user","sessionId":"'
                + session_id
                + '","message":{"content":[{"type":"text","text":"hello"}]}}\n'
            ),
            encoding="utf-8",
        )
    os.utime(older, (1000, 1000))
    os.utime(newer, (2000, 2000))

    sessions = parse_storage(tmp_path, limit=1)

    assert len(sessions) == 1
    assert sessions[0].session_id == "newer"


def test_decode_project_path_windows() -> None:
    encoded = "c--Users-EL035-dataschool-RealMe-OPIc"
    result = decode_project_path(encoded)

    assert result.startswith("C:/")
    assert "Users" in result
    assert "EL035" in result
