from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from geond.adapters.paths import newest_first_key

SOURCE = "codex"
NON_SESSION_JSONL_NAMES = {"history.jsonl", "session_index.jsonl"}
DEFAULT_SESSIONS_ROOT = Path.home() / ".codex" / "sessions"


@dataclass(frozen=True)
class ParsedCodexEvent:
    ordinal: int
    timestamp: str | None
    record_type: str
    event_type: str
    content: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class ParsedCodexMessage:
    ordinal: int
    role: str
    content: str
    timestamp: str | None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedCodexSession:
    session_id: str
    title: str
    session_path: Path
    metadata: dict[str, Any]
    events: list[ParsedCodexEvent]
    messages: list[ParsedCodexMessage]


def parse_storage(
    storage_path: Path,
    session_id: str | None = None,
    limit: int | None = None,
) -> list[ParsedCodexSession]:
    storage_path = storage_path.expanduser().resolve()
    index = read_session_index(storage_path)
    paths = candidate_session_paths(storage_path, session_id=session_id)
    sessions: list[ParsedCodexSession] = []

    for path in paths:
        session = parse_session_file(path, title_index=index)
        if session_id and session.session_id != session_id:
            continue
        sessions.append(session)
        if limit is not None and len(sessions) >= limit:
            break

    return sessions


def candidate_session_paths(storage_path: Path, session_id: str | None = None) -> list[Path]:
    if storage_path.is_file():
        return [storage_path] if storage_path.suffix == ".jsonl" else []

    paths = sorted(
        (
            path
            for path in storage_path.rglob("*.jsonl")
            if path.name not in NON_SESSION_JSONL_NAMES
        ),
        key=newest_first_key,
    )
    if not session_id:
        return paths
    return [path for path in paths if session_id in path.name]


def latest_session_path(storage_path: Path | None = None) -> Path | None:
    root = (storage_path or DEFAULT_SESSIONS_ROOT).expanduser().resolve()
    paths = candidate_session_paths(root)
    return paths[0] if paths else None


def read_session_index(storage_path: Path) -> dict[str, str]:
    search_roots = [storage_path] if storage_path.is_dir() else [storage_path.parent]
    search_roots.extend(search_roots[0].parents)

    for root in search_roots:
        index_path = root / "session_index.jsonl"
        if index_path.exists():
            return parse_session_index(index_path)
    return {}


def parse_session_index(index_path: Path) -> dict[str, str]:
    titles: dict[str, str] = {}
    for raw_line in index_path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        try:
            raw = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        session_id = raw.get("id")
        title = raw.get("thread_name")
        if isinstance(session_id, str) and isinstance(title, str):
            titles[session_id] = title
    return titles


def parse_session_file(
    session_path: Path,
    title_index: dict[str, str] | None = None,
) -> ParsedCodexSession:
    events: list[ParsedCodexEvent] = []
    messages: list[ParsedCodexMessage] = []
    metadata: dict[str, Any] = {"session_file": str(session_path)}
    session_id = session_path.stem
    title = session_path.stem

    for ordinal, raw_line in enumerate(session_path.read_text(encoding="utf-8").splitlines()):
        if not raw_line.strip():
            continue
        try:
            raw = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        timestamp = raw.get("timestamp") if isinstance(raw.get("timestamp"), str) else None
        record_type = str(raw.get("type") or "unknown")
        payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
        event_type = infer_event_type(record_type, payload)
        content = extract_text(payload)

        if record_type == "session_meta":
            meta_payload = payload
            if isinstance(meta_payload.get("id"), str):
                session_id = meta_payload["id"]
            metadata.update(
                {
                    key: meta_payload[key]
                    for key in (
                        "cwd",
                        "originator",
                        "cli_version",
                        "source",
                        "model_provider",
                        "model",
                    )
                    if key in meta_payload
                }
            )

        message = message_from_payload(ordinal, timestamp, record_type, payload)
        if message is not None:
            messages.append(message)

        events.append(
            ParsedCodexEvent(
                ordinal=ordinal,
                timestamp=timestamp,
                record_type=record_type,
                event_type=event_type,
                content=content,
                raw=raw,
            )
        )

    if title_index and session_id in title_index:
        title = title_index[session_id]
    elif metadata.get("cwd"):
        title = Path(str(metadata["cwd"])).name

    return ParsedCodexSession(
        session_id=session_id,
        title=title,
        session_path=session_path,
        metadata=metadata,
        events=events,
        messages=messages,
    )


def infer_event_type(record_type: str, payload: dict[str, Any]) -> str:
    payload_type = payload.get("type")
    if isinstance(payload_type, str):
        return f"{record_type}.{payload_type}"
    event_type = payload.get("event_type") or payload.get("type")
    if isinstance(event_type, str):
        return f"{record_type}.{event_type}"
    return record_type


def message_from_payload(
    ordinal: int,
    timestamp: str | None,
    record_type: str,
    payload: dict[str, Any],
) -> ParsedCodexMessage | None:
    if record_type == "response_item" and payload.get("type") == "message":
        role = str(payload.get("role") or "unknown")
        content = extract_text(payload.get("content"))
        if not content:
            return None
        return ParsedCodexMessage(
            ordinal=ordinal,
            role=role,
            content=content,
            timestamp=timestamp,
            metadata={"source": "response_item"},
        )

    if record_type == "event_msg":
        event_type = payload.get("type")
        if event_type == "user_message":
            content = extract_text(payload.get("message"))
            role = "user"
        elif event_type == "agent_message":
            content = extract_text(payload.get("message"))
            role = "assistant"
        else:
            return None
        if not content:
            return None
        return ParsedCodexMessage(
            ordinal=ordinal,
            role=role,
            content=content,
            timestamp=timestamp,
            metadata={"source": "event_msg", "event_type": event_type},
        )

    return None


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
        preferred_keys = (
            "text",
            "input_text",
            "output_text",
            "message",
            "content",
            "summary",
            "arguments",
            "name",
        )
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


def to_summary(session: ParsedCodexSession) -> dict[str, Any]:
    return {
        "session_id": session.session_id,
        "title": session.title,
        "session_path": str(session.session_path),
        "event_count": len(session.events),
        "message_count": len(session.messages),
        "cwd": session.metadata.get("cwd"),
        "originator": session.metadata.get("originator"),
        "model_provider": session.metadata.get("model_provider"),
    }
