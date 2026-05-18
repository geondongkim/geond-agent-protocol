from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CHAT_INDEX_KEY = "chat.ChatSessionStore.index"
SOURCE = "vscode-copilot"


@dataclass(frozen=True)
class ParsedChatLine:
    ordinal: int
    kind: int | None
    content: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class ParsedTranscriptEvent:
    ordinal: int
    event_type: str
    content: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class ParsedEditingSession:
    state: dict[str, Any] | None = None
    content_count: int = 0
    content_hashes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ParsedCopilotSession:
    session_id: str
    title: str
    index_entry: dict[str, Any]
    chat_lines: list[ParsedChatLine]
    transcript_events: list[ParsedTranscriptEvent]
    editing_session: ParsedEditingSession

    @property
    def has_editing_context(self) -> bool:
        return self.editing_session.state is not None


def parse_storage(storage_path: Path, session_id: str | None = None) -> list[ParsedCopilotSession]:
    storage_path = storage_path.expanduser().resolve()
    index = read_chat_index(storage_path)
    chat_dir = storage_path / "chatSessions"

    if session_id:
        candidate_ids = [session_id]
    elif index:
        candidate_ids = sorted(index.keys())
    else:
        candidate_ids = sorted(path.stem for path in chat_dir.glob("*.jsonl"))

    sessions: list[ParsedCopilotSession] = []
    for candidate_id in candidate_ids:
        chat_file = chat_dir / f"{candidate_id}.jsonl"
        if not chat_file.exists():
            continue
        index_entry = index.get(candidate_id, {"sessionId": candidate_id})
        sessions.append(
            ParsedCopilotSession(
                session_id=candidate_id,
                title=str(index_entry.get("title") or candidate_id),
                index_entry=index_entry,
                chat_lines=parse_chat_session(chat_file),
                transcript_events=parse_transcript(storage_path, candidate_id),
                editing_session=parse_editing_session(storage_path, candidate_id),
            )
        )
    return sessions


def read_chat_index(storage_path: Path) -> dict[str, dict[str, Any]]:
    db_path = storage_path / "state.vscdb"
    if not db_path.exists():
        return {}

    uri = f"file:{db_path.as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        cur = conn.cursor()
        cur.execute("SELECT value FROM ItemTable WHERE key = ?", (CHAT_INDEX_KEY,))
        row = cur.fetchone()
    finally:
        try:
            conn.close()
        except UnboundLocalError:
            pass

    if not row:
        return {}
    data = json.loads(row[0])
    entries = data.get("entries", {})
    if not isinstance(entries, dict):
        return {}
    return entries


def parse_chat_session(chat_file: Path) -> list[ParsedChatLine]:
    lines: list[ParsedChatLine] = []
    for ordinal, raw_line in iter_jsonl_records(chat_file):
        raw = json.loads(raw_line)
        lines.append(
            ParsedChatLine(
                ordinal=ordinal,
                kind=raw.get("kind"),
                content=extract_text(raw.get("v")),
                raw=raw,
            )
        )
    return lines


def parse_transcript(storage_path: Path, session_id: str) -> list[ParsedTranscriptEvent]:
    transcript_file = storage_path / "GitHub.copilot-chat" / "transcripts" / f"{session_id}.jsonl"
    if not transcript_file.exists():
        return []

    events: list[ParsedTranscriptEvent] = []
    for ordinal, raw_line in iter_jsonl_records(transcript_file):
        raw = json.loads(raw_line)
        event_type = str(raw.get("type") or "unknown")
        events.append(
            ParsedTranscriptEvent(
                ordinal=ordinal,
                event_type=event_type,
                content=extract_text(raw.get("data")),
                raw=raw,
            )
        )
    return events


def iter_jsonl_records(path: Path) -> Iterator[tuple[int, str]]:
    with path.open("r", encoding="utf-8", newline="\n") as handle:
        for ordinal, raw_line in enumerate(handle):
            raw_line = raw_line.removesuffix("\n").removesuffix("\r")
            if raw_line.strip():
                yield ordinal, raw_line


def parse_editing_session(storage_path: Path, session_id: str) -> ParsedEditingSession:
    session_dir = storage_path / "chatEditingSessions" / session_id
    state_file = session_dir / "state.json"
    contents_dir = session_dir / "contents"
    if not state_file.exists():
        return ParsedEditingSession()

    state = json.loads(state_file.read_text(encoding="utf-8"))
    content_hashes = (
        sorted(path.name for path in contents_dir.iterdir()) if contents_dir.exists() else []
    )
    return ParsedEditingSession(
        state=state,
        content_count=len(content_hashes),
        content_hashes=content_hashes,
    )


def extract_text(value: Any, max_depth: int = 6) -> str:
    if max_depth <= 0 or value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, int | float | bool):
        return str(value)
    if isinstance(value, list):
        return "\n".join(part for item in value if (part := extract_text(item, max_depth - 1)))
    if isinstance(value, dict):
        preferred_keys = ("text", "content", "message", "value", "inputText", "result", "summary")
        parts: list[str] = []
        for key in preferred_keys:
            if key in value:
                text = extract_text(value[key], max_depth - 1)
                if text:
                    parts.append(text)
        if parts:
            return "\n".join(parts)
        return "\n".join(
            part for item in value.values() if (part := extract_text(item, max_depth - 1))
        )
    return ""


def to_summary(session: ParsedCopilotSession) -> dict[str, Any]:
    return {
        "session_id": session.session_id,
        "title": session.title,
        "chat_line_count": len(session.chat_lines),
        "transcript_event_count": len(session.transcript_events),
        "has_editing_context": session.has_editing_context,
        "editing_content_count": session.editing_session.content_count,
    }
