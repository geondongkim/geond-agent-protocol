from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

TASK_GRAPH_SCHEMA = "geond.task_graph_input.v1"


def parse_task_graph_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    return parse_task_graph_text(text)


def parse_task_graph_text(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        return {"schema": TASK_GRAPH_SCHEMA, "tasks": []}
    if stripped.startswith("{"):
        payload = json.loads(stripped)
        return normalize_task_graph_payload(payload)
    return parse_markdown_task_graph(stripped)


def normalize_task_graph_payload(payload: dict[str, Any]) -> dict[str, Any]:
    tasks = payload.get("tasks") or []
    if not isinstance(tasks, list):
        raise ValueError("task graph JSON must contain a tasks list")
    return {
        "schema": TASK_GRAPH_SCHEMA,
        "tasks": [normalize_task_graph_task(item) for item in tasks if isinstance(item, dict)],
    }


def normalize_task_graph_task(item: dict[str, Any]) -> dict[str, Any]:
    key = str(item.get("key") or "").strip()
    title = str(item.get("title") or "").strip()
    if not key or not title:
        raise ValueError("each task graph item must include key and title")
    depends_on = item.get("depends_on") or []
    if isinstance(depends_on, str):
        depends_on = [value.strip() for value in depends_on.split(",") if value.strip()]
    return {
        "key": key,
        "title": title,
        "description": str(item.get("description") or ""),
        "priority": int(item.get("priority") or 0),
        "status": str(item.get("status") or "ready"),
        "depends_on": [str(value).strip() for value in depends_on if str(value).strip()],
        "required_evidence": item.get("required_evidence") or [],
    }


def parse_markdown_task_graph(text: str) -> dict[str, Any]:
    tasks: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or not line.startswith("-"):
            continue
        line = re.sub(r"^-\s*(?:\[[ xX]\]\s*)?", "", line)
        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 2:
            raise ValueError("markdown task graph lines must use: key | title | options")
        key, title = parts[0], parts[1]
        options = parse_markdown_options(parts[2:])
        tasks.append(
            normalize_task_graph_task(
                {
                    "key": key,
                    "title": title,
                    "description": options.get("description", ""),
                    "priority": options.get("priority", 0),
                    "depends_on": options.get("depends_on", []),
                    "status": options.get("status", "ready"),
                }
            )
        )
    return {"schema": TASK_GRAPH_SCHEMA, "tasks": tasks}


def parse_markdown_options(parts: list[str]) -> dict[str, Any]:
    options: dict[str, Any] = {}
    for part in parts:
        key, sep, value = part.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if key == "priority":
            options[key] = int(value or 0)
        elif key == "depends_on":
            options[key] = [item.strip() for item in value.split(",") if item.strip()]
        else:
            options[key] = value
    return options
