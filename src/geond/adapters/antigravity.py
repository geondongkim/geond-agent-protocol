from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from geond.adapters.paths import newest_first_key

SOURCE = "antigravity"
DEFAULT_TRANSCRIPT_ROOT = Path.home() / ".gemini" / "antigravity-cli" / "brain"


@dataclass(frozen=True)
class ParsedAntigravityEvent:
    ordinal: int
    timestamp: str | None
    source: str
    record_type: str
    status: str | None
    content: str
    tool_calls: list[dict[str, Any]]
    raw: dict[str, Any]


@dataclass(frozen=True)
class ParsedAntigravityMessage:
    ordinal: int
    role: str
    content: str
    timestamp: str | None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedAntigravitySession:
    session_id: str
    title: str
    session_path: Path
    metadata: dict[str, Any]
    events: list[ParsedAntigravityEvent]
    messages: list[ParsedAntigravityMessage]


def parse_storage(
    storage_path: Path | None = None,
    session_id: str | None = None,
    limit: int | None = None,
) -> list[ParsedAntigravitySession]:
    root = (storage_path or DEFAULT_TRANSCRIPT_ROOT).expanduser().resolve()
    paths = candidate_transcript_paths(root, session_id=session_id)
    sessions: list[ParsedAntigravitySession] = []
    for path in paths:
        session = parse_transcript_file(path)
        if session_id and session.session_id != session_id:
            continue
        sessions.append(session)
        if limit is not None and len(sessions) >= limit:
            break
    return sessions


def candidate_transcript_paths(root: Path, session_id: str | None = None) -> list[Path]:
    if root.is_file():
        return [root] if root.name == "transcript.jsonl" or root.suffix == ".jsonl" else []
    paths = sorted(root.rglob("transcript.jsonl"), key=newest_first_key)
    if session_id:
        return [path for path in paths if session_id in str(path)]
    return paths


def parse_transcript_file(session_path: Path) -> ParsedAntigravitySession:
    events: list[ParsedAntigravityEvent] = []
    messages: list[ParsedAntigravityMessage] = []
    metadata: dict[str, Any] = {"session_file": str(session_path)}
    session_id = infer_session_id(session_path)

    for ordinal, raw_line in enumerate(session_path.read_text(encoding="utf-8").splitlines()):
        if not raw_line.strip():
            continue
        try:
            raw = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue

        timestamp = first_string(raw, "created_at", "timestamp", "time")
        source = first_string(raw, "source", "actor", "role") or "unknown"
        record_type = first_string(raw, "type", "event_type", "kind") or "unknown"
        status = first_string(raw, "status")
        content = extract_text(raw.get("content"))
        model = find_first_string(raw, {"model", "model_name", "model_label", "selected_model"})
        if model and "model" not in metadata:
            metadata["model"] = model
        if "step_index" in raw:
            metadata["last_step_index"] = raw.get("step_index")
        tool_calls = normalize_tool_calls(raw.get("tool_calls"))

        events.append(
            ParsedAntigravityEvent(
                ordinal=ordinal,
                timestamp=timestamp,
                source=source,
                record_type=record_type,
                status=status,
                content=content,
                tool_calls=tool_calls,
                raw=raw,
            )
        )
        message = message_from_event(ordinal, timestamp, source, record_type, content, tool_calls)
        if message is not None:
            messages.append(message)

    title = (
        session_path.parent.parent.parent.name
        if ".system_generated" in session_path.parts
        else session_id
    )
    metadata["event_count"] = len(events)
    metadata["message_count"] = len(messages)
    metadata["tool_call_count"] = sum(len(event.tool_calls) for event in events)
    return ParsedAntigravitySession(
        session_id=session_id,
        title=title,
        session_path=session_path,
        metadata=metadata,
        events=events,
        messages=messages,
    )


def infer_session_id(path: Path) -> str:
    parts = list(path.parts)
    if ".system_generated" in parts:
        index = parts.index(".system_generated")
        if index > 0:
            return parts[index - 1]
    if path.parent.name == "logs" and path.parent.parent.name == ".system_generated":
        return path.parent.parent.parent.name
    return path.stem


def message_from_event(
    ordinal: int,
    timestamp: str | None,
    source: str,
    record_type: str,
    content: str,
    tool_calls: list[dict[str, Any]],
) -> ParsedAntigravityMessage | None:
    if not content:
        return None
    role = role_from_source(source, record_type)
    if role not in {"user", "assistant"}:
        return None
    return ParsedAntigravityMessage(
        ordinal=ordinal,
        role=role,
        content=content,
        timestamp=timestamp,
        metadata={"source": source, "type": record_type, "tool_call_count": len(tool_calls)},
    )


def role_from_source(source: str, record_type: str) -> str:
    normalized = f"{source} {record_type}".casefold()
    if "user" in normalized or "human" in normalized:
        return "user"
    if any(token in normalized for token in ("model", "assistant", "agent")):
        return "assistant"
    return "unknown"


def normalize_tool_calls(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def first_string(value: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def find_first_string(value: Any, keys: set[str], max_depth: int = 6) -> str | None:
    if max_depth <= 0:
        return None
    if isinstance(value, dict):
        for key, candidate in value.items():
            if key in keys and isinstance(candidate, str) and candidate:
                return candidate
        for candidate in value.values():
            found = find_first_string(candidate, keys, max_depth - 1)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = find_first_string(item, keys, max_depth - 1)
            if found:
                return found
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
        parts = []
        for key in ("text", "message", "content", "summary", "output"):
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


def to_summary(session: ParsedAntigravitySession) -> dict[str, Any]:
    return {
        "session_id": session.session_id,
        "title": session.title,
        "session_path": str(session.session_path),
        "event_count": len(session.events),
        "message_count": len(session.messages),
        "tool_call_count": session.metadata.get("tool_call_count", 0),
        "model": session.metadata.get("model"),
    }
