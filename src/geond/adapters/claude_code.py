from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from geond.adapters.paths import newest_first_key

SOURCE = "claude-code"
PROJECTS_DIR = Path.home() / ".claude" / "projects"

_META_KEYS = ("cwd", "gitBranch", "version", "sessionId")


@dataclass(frozen=True)
class ParsedClaudeCodeEvent:
    ordinal: int
    record_type: str
    content: str
    tool_name: str | None
    tool_input: dict[str, Any]
    uuid: str
    parent_uuid: str | None
    timestamp: str | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class ParsedClaudeCodeMessage:
    ordinal: int
    role: str
    content: str
    timestamp: str | None
    tool_calls: list[dict[str, Any]]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedClaudeCodeSession:
    session_id: str
    session_path: Path
    metadata: dict[str, Any]
    events: list[ParsedClaudeCodeEvent]
    messages: list[ParsedClaudeCodeMessage]


def parse_storage(
    storage_path: Path | None = None,
    session_id: str | None = None,
    limit: int | None = None,
) -> list[ParsedClaudeCodeSession]:
    root = (storage_path or PROJECTS_DIR).expanduser().resolve()
    paths = candidate_session_paths(root, session_id=session_id)
    sessions: list[ParsedClaudeCodeSession] = []
    for path in paths:
        session = parse_session_file(path)
        if session_id and session.session_id != session_id:
            continue
        sessions.append(session)
        if limit is not None and len(sessions) >= limit:
            break
    return sessions


def candidate_session_paths(storage_path: Path, session_id: str | None = None) -> list[Path]:
    if storage_path.is_file():
        return [storage_path] if storage_path.suffix == ".jsonl" else []
    paths = sorted(storage_path.rglob("*.jsonl"), key=newest_first_key)
    if session_id:
        return [p for p in paths if session_id in p.stem]
    return paths


def parse_session_file(session_path: Path) -> ParsedClaudeCodeSession:
    events: list[ParsedClaudeCodeEvent] = []
    messages: list[ParsedClaudeCodeMessage] = []
    metadata: dict[str, Any] = {"session_file": str(session_path)}
    session_id = session_path.stem

    for ordinal, raw_bytes in enumerate(session_path.read_bytes().split(b"\n")):
        line = raw_bytes.strip()
        if not line:
            continue
        try:
            raw = json.loads(line.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue

        record_type = str(raw.get("type") or "unknown")
        timestamp = raw.get("timestamp") if isinstance(raw.get("timestamp"), str) else None
        uuid = str(raw.get("uuid") or "")
        parent_uuid = raw.get("parentUuid") if isinstance(raw.get("parentUuid"), str) else None

        if record_type == "user" and "cwd" not in metadata:
            metadata.update({k: raw[k] for k in _META_KEYS if k in raw})
            if isinstance(raw.get("sessionId"), str):
                session_id = raw["sessionId"]

        blocks = _get_content_blocks(raw, record_type)
        text_content = _extract_text(blocks)
        tool_calls = _extract_tool_calls(blocks)

        events.append(
            ParsedClaudeCodeEvent(
                ordinal=ordinal,
                record_type=record_type,
                content=text_content,
                tool_name=tool_calls[0]["name"] if tool_calls else None,
                tool_input=tool_calls[0]["input"] if tool_calls else {},
                uuid=uuid,
                parent_uuid=parent_uuid,
                timestamp=timestamp,
                raw=raw,
            )
        )

        if record_type in ("user", "assistant") and text_content:
            messages.append(
                ParsedClaudeCodeMessage(
                    ordinal=ordinal,
                    role="user" if record_type == "user" else "assistant",
                    content=text_content,
                    timestamp=timestamp,
                    tool_calls=tool_calls,
                    metadata={"uuid": uuid, "parent_uuid": parent_uuid},
                )
            )

    return ParsedClaudeCodeSession(
        session_id=session_id,
        session_path=session_path,
        metadata=metadata,
        events=events,
        messages=messages,
    )


def _get_content_blocks(raw: dict[str, Any], record_type: str) -> list[dict[str, Any]]:
    if record_type in ("user", "assistant"):
        msg = raw.get("message")
        if isinstance(msg, dict):
            content = msg.get("content")
            if isinstance(content, list):
                return [b for b in content if isinstance(b, dict)]
    return []


def _extract_text(blocks: list[dict[str, Any]]) -> str:
    parts = [b["text"] for b in blocks if b.get("type") == "text" and b.get("text")]
    return "\n".join(parts)


def _extract_tool_calls(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"name": b.get("name"), "input": b.get("input") or {}, "id": b.get("id")}
        for b in blocks
        if b.get("type") == "tool_use"
    ]


def decode_project_path(encoded: str) -> str:
    """Best-effort decode of ~/.claude/projects/{encoded} dir name to CWD.

    Encoding is lossy (both '-' and '_' map to '-'), so use cwd from JSONL records
    for the authoritative workspace path.
    """
    if len(encoded) < 3 or encoded[1:3] != "--":
        return encoded
    drive = encoded[0].upper()
    rest = encoded[3:].replace("-", "/")
    return f"{drive}:/{rest}"


def to_summary(session: ParsedClaudeCodeSession) -> dict[str, Any]:
    return {
        "session_id": session.session_id,
        "session_path": str(session.session_path),
        "event_count": len(session.events),
        "message_count": len(session.messages),
        "cwd": session.metadata.get("cwd"),
        "git_branch": session.metadata.get("gitBranch"),
        "version": session.metadata.get("version"),
    }
