from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

SOURCE = "manus"
SOURCE_ADAPTER = "manus_api_v2"
AGENT_NAME = "Manus"

_MANUS_API_BASE = "https://api.manus.ai"
_MAX_RETRIES = 3
_RETRY_BASE_SECONDS = 1.0


@dataclass(frozen=True)
class ParsedManusMessage:
    ordinal: int
    message_id: str
    role: str
    content: str
    created_at: str | None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedManusTask:
    task_id: str
    title: str
    status: str
    created_at: str | None
    updated_at: str | None
    task_url: str | None
    share_url: str | None
    share_visibility: str
    project_id: str | None
    connector_ids: list[str]
    messages: list[ParsedManusMessage]
    metadata: dict[str, Any] = field(default_factory=dict)


def normalize_task(
    task_detail: dict[str, Any],
    task_messages: dict[str, Any] | None = None,
) -> ParsedManusTask:
    """Convert raw Manus API JSON into a ParsedManusTask."""
    task_id = str(task_detail.get("task_id", ""))
    raw_messages = (task_messages or {}).get("messages") or []
    messages = _normalize_messages(task_id, raw_messages)

    connector_ids = [str(c) for c in (task_detail.get("connectors") or []) if c]

    extra_meta = {
        k: v
        for k, v in task_detail.items()
        if k
        not in {
            "task_id",
            "task_title",
            "status",
            "created_at",
            "updated_at",
            "task_url",
            "share_url",
            "share_visibility",
            "project_id",
            "connectors",
        }
    }

    return ParsedManusTask(
        task_id=task_id,
        title=str(task_detail.get("task_title") or task_id),
        status=str(task_detail.get("status") or "unknown"),
        created_at=task_detail.get("created_at"),
        updated_at=task_detail.get("updated_at"),
        task_url=task_detail.get("task_url"),
        share_url=task_detail.get("share_url"),
        share_visibility=str(task_detail.get("share_visibility") or "private"),
        project_id=task_detail.get("project_id"),
        connector_ids=connector_ids,
        messages=messages,
        metadata={
            "source_adapter": SOURCE_ADAPTER,
            "project_id": task_detail.get("project_id"),
            **extra_meta,
        },
    )


def _normalize_messages(
    task_id: str,
    raw_messages: list[dict[str, Any]],
) -> list[ParsedManusMessage]:
    result: list[ParsedManusMessage] = []
    for ordinal, raw in enumerate(raw_messages, start=1):
        if not isinstance(raw, dict):
            continue
        message_id = str(raw.get("id") or f"{task_id}:{ordinal}")
        role = _normalize_role(raw.get("role"))
        content = str(raw.get("content") or "")
        result.append(
            ParsedManusMessage(
                ordinal=ordinal,
                message_id=message_id,
                role=role,
                content=content,
                created_at=raw.get("created_at"),
                raw={
                    k: v for k, v in raw.items() if k not in {"id", "content", "role", "created_at"}
                },
            )
        )
    return result


def _normalize_role(raw_role: Any) -> str:
    role = str(raw_role or "").lower()
    if role == "user":
        return "user"
    if role == "assistant":
        return "assistant"
    return "assistant_or_tool"


def load_fixture(detail_path: str, messages_path: str | None = None) -> ParsedManusTask:
    with open(detail_path, encoding="utf-8") as f:
        detail = json.load(f)
    messages: dict[str, Any] | None = None
    if messages_path:
        with open(messages_path, encoding="utf-8") as f:
            messages = json.load(f)
    return normalize_task(detail, messages)


class ManusApiError(Exception):
    def __init__(
        self,
        status_code: int,
        endpoint: str,
        message: str,
        request_id: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.endpoint = endpoint
        self.request_id = request_id
        super().__init__(f"Manus API error {status_code} at {endpoint}: {message}")


class ManusApiClient:
    """Minimal read-only client for the Manus API v2.

    Auth via MANUS_API_KEY environment variable.
    Never logs the key.
    """

    def __init__(self, api_key: str | None = None, base_url: str = _MANUS_API_BASE) -> None:
        self._api_key = api_key or os.environ.get("MANUS_API_KEY") or ""
        self._base_url = base_url.rstrip("/")

    def _request(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        query = ""
        if params:
            query = "?" + "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{self._base_url}/{path.lstrip('/')}{query}"
        req = urllib.request.Request(url)
        req.add_header("x-manus-api-key", self._api_key)
        req.add_header("Accept", "application/json")

        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    body = resp.read().decode("utf-8")
                    return json.loads(body)
            except urllib.error.HTTPError as exc:
                request_id = exc.headers.get("x-request-id") if exc.headers else None
                if exc.code == 429:
                    wait = _RETRY_BASE_SECONDS * (2**attempt)
                    time.sleep(wait)
                    last_exc = ManusApiError(exc.code, url, "rate_limited", request_id)
                    continue
                body_text = ""
                try:
                    body_text = exc.read().decode("utf-8", errors="replace")[:200]
                except Exception:
                    pass
                error_map = {
                    403: "permission_denied — check your API key permissions",
                    400: f"invalid_argument — {body_text}",
                    404: "not_found — check the task_id",
                }
                msg = error_map.get(exc.code, body_text or "unexpected error")
                raise ManusApiError(exc.code, url, msg, request_id) from exc
            except Exception as exc:
                raise ManusApiError(0, url, str(exc)) from exc

        assert last_exc is not None
        raise last_exc

    def get_task(self, task_id: str) -> dict[str, Any]:
        return self._request("v2/task.detail", {"task_id": task_id})

    def get_task_messages(self, task_id: str, limit: int = 200) -> dict[str, Any]:
        return self._request(
            "v2/task.listMessages",
            {"task_id": task_id, "order": "asc", "limit": str(limit)},
        )

    def list_tasks(self, limit: int = 20) -> dict[str, Any]:
        return self._request("v2/task.list", {"limit": str(limit)})

    def fetch_task(self, task_id: str) -> ParsedManusTask:
        detail = self.get_task(task_id)
        messages = self.get_task_messages(task_id)
        return normalize_task(detail, messages)
