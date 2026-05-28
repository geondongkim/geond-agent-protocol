from __future__ import annotations

import os
from pathlib import Path

from geond.adapters.antigravity import parse_storage, parse_transcript_file, to_summary

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "antigravity" / "brain"
FIXTURE_TRANSCRIPT = (
    FIXTURE_ROOT / "agy-session-1" / ".system_generated" / "logs" / "transcript.jsonl"
)


def test_parse_antigravity_transcript_extracts_messages_and_tool_calls() -> None:
    session = parse_transcript_file(FIXTURE_TRANSCRIPT)

    assert session.session_id == "agy-session-1"
    assert session.metadata["model"] == "Gemini 3.5 Flash (Medium)"
    assert len(session.events) == 3
    assert [message.role for message in session.messages] == [
        "user",
        "assistant",
        "assistant",
    ]
    assert session.events[-1].tool_calls[0]["name"] == "shell"
    assert to_summary(session)["tool_call_count"] == 1


def test_parse_antigravity_storage_uses_default_transcript_name_and_limit() -> None:
    sessions = parse_storage(FIXTURE_ROOT, limit=1)

    assert len(sessions) == 1
    assert sessions[0].session_id == "agy-session-1"


def test_parse_antigravity_storage_limits_to_newest_transcript(tmp_path: Path) -> None:
    older = tmp_path / "older" / ".system_generated" / "logs" / "transcript.jsonl"
    newer = tmp_path / "newer" / ".system_generated" / "logs" / "transcript.jsonl"
    for path, content in (
        (older, '{"source":"user","type":"message","content":"old"}\n'),
        (newer, '{"source":"user","type":"message","content":"new"}\n'),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    os.utime(older, (1000, 1000))
    os.utime(newer, (2000, 2000))

    sessions = parse_storage(tmp_path, limit=1)

    assert len(sessions) == 1
    assert sessions[0].session_id == "newer"


def test_parse_antigravity_transcript_normalizes_wrapped_user_content(
    tmp_path: Path,
) -> None:
    transcript = tmp_path / "wrapped-session" / ".system_generated" / "logs" / "transcript.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        "\n".join(
            [
                (
                    '{"source":"user","type":"message","content":'
                    '"<USER_REQUEST>\\nPlease verify GEOND_WRAPPED_MARKER.\\n'
                    "Keep the searchable text compact.\\n</USER_REQUEST>\\n"
                    '<ADDITIONAL_METADATA>\\nwindow=Antigravity\\n</ADDITIONAL_METADATA>"}'
                ),
                (
                    '{"source":"user","type":"message","content":'
                    '"<USER_SETTINGS_CHANGE>\\ntheme=dark\\n</USER_SETTINGS_CHANGE>"}'
                ),
                '{"source":"user","type":"message","content":"Plain text fallback"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    session = parse_transcript_file(transcript)

    assert [message.content for message in session.messages] == [
        "Please verify GEOND_WRAPPED_MARKER.\nKeep the searchable text compact.",
        "Plain text fallback",
    ]
    assert session.messages[0].metadata["content_normalized"] is True
    assert isinstance(session.messages[0].metadata["raw_content_sha256"], str)
    assert "<ADDITIONAL_METADATA>" in session.events[0].content
