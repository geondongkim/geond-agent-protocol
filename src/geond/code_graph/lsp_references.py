from __future__ import annotations

from typing import Any
from urllib.parse import unquote, urlparse


def normalize_lsp_references(
    data: Any,
    *,
    workspace_root: str | None = None,
    target_qualified_name: str | None = None,
    provider: str | None = None,
) -> list[dict[str, Any]]:
    target = target_qualified_name or lsp_target_qualified_name(data)
    root = workspace_root or lsp_workspace_root(data)
    resolved_provider = provider or lsp_provider(data)
    items = lsp_reference_items(data)
    if not looks_like_lsp_locations(items):
        return [dict(item) for item in items if isinstance(item, dict)]
    references: list[dict[str, Any]] = []
    for item in items:
        reference = normalize_lsp_location(item, root)
        if reference is None:
            continue
        output: dict[str, Any] = {
            "reference": reference,
            "metadata": {"lsp": lsp_location_metadata(item)},
        }
        if target:
            output["target_qualified_name"] = target
        if resolved_provider:
            output["provider"] = resolved_provider
        references.append(output)
    return references


def lsp_reference_items(data: Any) -> list[Any]:
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in ("references", "locations", "items", "result"):
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


def looks_like_lsp_locations(items: list[Any]) -> bool:
    return bool(items) and all(is_lsp_location(item) for item in items)


def is_lsp_location(item: Any) -> bool:
    return isinstance(item, dict) and (
        "uri" in item or "targetUri" in item or ("range" in item and "reference" not in item)
    )


def normalize_lsp_location(
    item: dict[str, Any], workspace_root: str | None
) -> dict[str, Any] | None:
    uri = lsp_location_uri(item)
    range_value = item.get("range") or item.get("targetSelectionRange") or item.get("targetRange")
    if not uri or not isinstance(range_value, dict):
        return None
    start = lsp_position(range_value.get("start"))
    end = lsp_position(range_value.get("end")) or start
    if start is None:
        return None
    reference: dict[str, Any] = {
        "file_path": relative_file_path(uri, workspace_root),
        "start_line": start["line"] + 1,
        "start_character": start.get("character"),
    }
    if end is not None:
        reference["end_line"] = end["line"] + 1
        reference["end_character"] = end.get("character")
    return reference


def lsp_location_uri(item: dict[str, Any]) -> str | None:
    value = item.get("uri") or item.get("targetUri")
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("external", "fsPath", "path"):
            nested = value.get(key)
            if isinstance(nested, str) and nested:
                return nested
    return None


def lsp_position(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    try:
        position = {"line": int(value["line"])}
    except (KeyError, TypeError, ValueError):
        return None
    try:
        position["character"] = int(value.get("character", 0))
    except (TypeError, ValueError):
        position["character"] = 0
    return position


def relative_file_path(uri_or_path: str, workspace_root: str | None) -> str:
    path = normalized_path(uri_or_path)
    root = normalized_path(workspace_root) if workspace_root else ""
    if root:
        root_prefix = root.rstrip("/") + "/"
        if path.lower().startswith(root_prefix.lower()):
            return path[len(root_prefix) :]
    return path


def normalized_path(uri_or_path: str | None) -> str:
    if not uri_or_path:
        return ""
    parsed = urlparse(uri_or_path)
    if parsed.scheme == "file":
        path = unquote(parsed.path)
        if parsed.netloc:
            path = f"//{parsed.netloc}{path}"
    else:
        path = uri_or_path
    if len(path) >= 3 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    return path.replace("\\", "/").rstrip("/")


def lsp_target_qualified_name(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    value = data.get("target_qualified_name") or data.get("qualified_name")
    if isinstance(value, str) and value.strip():
        return value.strip()
    target = data.get("target")
    if isinstance(target, dict):
        value = target.get("qualified_name") or target.get("target_qualified_name")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def lsp_workspace_root(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    value = data.get("workspace_root") or data.get("workspace_uri") or data.get("root_uri")
    return value if isinstance(value, str) and value else None


def lsp_provider(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    value = data.get("provider")
    return value if isinstance(value, str) and value else None


def lsp_location_metadata(item: dict[str, Any]) -> dict[str, Any]:
    metadata = {"range": item.get("range")}
    uri = lsp_location_uri(item)
    if uri:
        metadata["uri"] = uri
    if "targetSelectionRange" in item:
        metadata["targetSelectionRange"] = item["targetSelectionRange"]
    if "targetRange" in item:
        metadata["targetRange"] = item["targetRange"]
    return {key: value for key, value in metadata.items() if value is not None}
