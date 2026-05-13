from __future__ import annotations

import json
import os
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

SUPPORTED_INSTALL_CLIENTS = (
    "vscode-mcp",
    "claude-desktop",
    "continue",
    "vscode-lsp-task",
)
DEFAULT_INSTALL_CLIENTS = ("vscode-mcp", "vscode-lsp-task")
DEFAULT_DATABASE_URL = "postgresql://geond:geond_dev_password@localhost:55432/geond"
DEFAULT_DATABASE_PROFILE = "local"
LSP_TASK_LABEL = "Geond: collect LSP references"


def expand_install_clients(clients: list[str] | None) -> list[str]:
    selected = clients or list(DEFAULT_INSTALL_CLIENTS)
    if "all" in selected:
        return list(SUPPORTED_INSTALL_CLIENTS)
    deduped: list[str] = []
    for client in selected:
        if client not in SUPPORTED_INSTALL_CLIENTS:
            raise ValueError(f"Unsupported install client: {client}")
        if client not in deduped:
            deduped.append(client)
    return deduped


def path_for_config(client: str, workspace_root: Path) -> Path:
    if client == "vscode-mcp":
        return workspace_root / ".vscode" / "mcp.json"
    if client == "vscode-lsp-task":
        return workspace_root / ".vscode" / "tasks.json"
    if client == "continue":
        return Path.home() / ".continue" / "config.yaml"
    if client == "claude-desktop":
        if sys.platform == "win32":
            appdata = os.environ.get("APPDATA")
            base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
            return base / "Claude" / "claude_desktop_config.json"
        if sys.platform == "darwin":
            return (
                Path.home()
                / "Library"
                / "Application Support"
                / "Claude"
                / "claude_desktop_config.json"
            )
        return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"
    raise ValueError(f"Unsupported install client: {client}")


def mcp_server_entry(
    repo_root: Path,
    *,
    database_url: str = DEFAULT_DATABASE_URL,
    database_profile: str = DEFAULT_DATABASE_PROFILE,
    privacy_mode: str = "local-only",
    embedding_provider: str = "none",
    embedding_model: str | None = None,
    include_type: bool = False,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "command": "uv",
        "args": ["--directory", repo_root.resolve().as_posix(), "run", "geond-mcp"],
        "env": {
            "GEOND_DATABASE_PROFILE": database_profile,
            database_url_env_key(database_profile): database_url,
            "GEOND_PRIVACY_MODE": privacy_mode,
            "GEOND_EMBEDDING_PROVIDER": embedding_provider,
        },
    }
    if embedding_model:
        entry["env"]["GEOND_EMBEDDING_MODEL"] = embedding_model
    if include_type:
        entry = {"type": "stdio", **entry}
    return entry


def database_url_env_key(profile: str) -> str:
    normalized = re.sub(r"[^A-Z0-9]+", "_", profile.upper()).strip("_")
    if not normalized or normalized == "LOCAL":
        return "GEOND_DATABASE_URL"
    if normalized == "AZURE":
        return "AZURE_GEOND_DATABASE_URL"
    return f"GEOND_DATABASE_URL_{normalized}"


def vscode_mcp_config(
    existing: dict[str, Any], server_name: str, entry: dict[str, Any]
) -> dict[str, Any]:
    merged = deepcopy(existing)
    servers = merged.setdefault("servers", {})
    if not isinstance(servers, dict):
        servers = {}
        merged["servers"] = servers
    servers[server_name] = entry
    return merged


def claude_desktop_config(
    existing: dict[str, Any], server_name: str, entry: dict[str, Any]
) -> dict[str, Any]:
    merged = deepcopy(existing)
    servers = merged.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        servers = {}
        merged["mcpServers"] = servers
    servers[server_name] = entry
    return merged


def vscode_lsp_task_config(existing: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(existing) if existing else {"version": "2.0.0"}
    merged.setdefault("version", "2.0.0")
    tasks = merged.setdefault("tasks", [])
    inputs = merged.setdefault("inputs", [])
    if not isinstance(tasks, list):
        tasks = []
        merged["tasks"] = tasks
    if not isinstance(inputs, list):
        inputs = []
        merged["inputs"] = inputs

    merge_by_key(tasks, vscode_lsp_task(), "label")
    for item in vscode_lsp_inputs():
        merge_by_key(inputs, item, "id")
    return merged


def merge_by_key(items: list[Any], new_item: dict[str, Any], key: str) -> None:
    for index, item in enumerate(items):
        if isinstance(item, dict) and item.get(key) == new_item[key]:
            items[index] = new_item
            return
    items.append(new_item)


def vscode_lsp_task() -> dict[str, Any]:
    return {
        "label": LSP_TASK_LABEL,
        "type": "shell",
        "command": "uv",
        "args": [
            "--directory",
            "${workspaceFolder}",
            "run",
            "geond",
            "collect-lsp-references",
            "${input:targetFile}",
            "--line",
            "${input:targetLine}",
            "--character",
            "${input:targetCharacter}",
            "--workspace-root",
            "${workspaceFolder}",
            "--server-profile",
            "${input:serverProfile}",
            "--target-qualified-name",
            "${input:targetQualifiedName}",
            "--output",
            "${workspaceFolder}/references.json",
        ],
        "problemMatcher": [],
    }


def vscode_lsp_inputs() -> list[dict[str, Any]]:
    return [
        {
            "id": "targetFile",
            "type": "promptString",
            "description": "File containing the target symbol, relative to the workspace root",
        },
        {"id": "targetLine", "type": "promptString", "description": "1-based target line"},
        {
            "id": "targetCharacter",
            "type": "promptString",
            "description": "0-based target character",
            "default": "0",
        },
        {
            "id": "targetQualifiedName",
            "type": "promptString",
            "description": "Geond qualified name, for example service.build_answer",
        },
        {
            "id": "serverProfile",
            "type": "pickString",
            "description": "Built-in stdio language-server profile",
            "options": ["auto", "pyright", "typescript"],
            "default": "auto",
        },
    ]


def render_continue_config(server_name: str, entry: dict[str, Any]) -> str:
    lines = ["mcpServers:", f"  - name: {yaml_scalar(server_name)}"]
    lines.append(f"    command: {yaml_scalar(entry['command'])}")
    lines.append("    args:")
    for arg in entry.get("args", []):
        lines.append(f"      - {yaml_scalar(arg)}")
    lines.append("    env:")
    for key, value in entry.get("env", {}).items():
        lines.append(f"      {key}: {yaml_scalar(value)}")
    return "\n".join(lines) + "\n"


def yaml_scalar(value: object) -> str:
    return json.dumps(str(value))


def load_json_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        return {}
    loaded = json.loads(content)
    return loaded if isinstance(loaded, dict) else {}


def write_json_config(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def install_clients(
    clients: list[str] | None = None,
    *,
    repo_root: Path,
    workspace_root: Path,
    config_path: Path | None = None,
    server_name: str = "geond",
    database_url: str = DEFAULT_DATABASE_URL,
    database_profile: str = DEFAULT_DATABASE_PROFILE,
    privacy_mode: str = "local-only",
    embedding_provider: str = "none",
    embedding_model: str | None = None,
    write: bool = False,
    overwrite: bool = False,
) -> dict[str, Any]:
    expanded = expand_install_clients(clients)
    if config_path and len(expanded) != 1:
        raise ValueError("--config-path can only be used with one --client")

    results: list[dict[str, Any]] = []
    for client in expanded:
        path = config_path or path_for_config(client, workspace_root)
        result = install_one_client(
            client,
            path=path,
            repo_root=repo_root,
            server_name=server_name,
            database_url=database_url,
            database_profile=database_profile,
            privacy_mode=privacy_mode,
            embedding_provider=embedding_provider,
            embedding_model=embedding_model,
            write=write,
            overwrite=overwrite,
        )
        results.append(result)
    status = "warning" if any(item["action"] == "skipped" for item in results) else "ok"
    return {"status": status, "write": write, "targets": results}


def install_one_client(
    client: str,
    *,
    path: Path,
    repo_root: Path,
    server_name: str,
    database_url: str,
    database_profile: str,
    privacy_mode: str,
    embedding_provider: str,
    embedding_model: str | None,
    write: bool,
    overwrite: bool,
) -> dict[str, Any]:
    existed_before = path.exists()
    if client == "vscode-lsp-task":
        existing = load_json_config(path)
        content = vscode_lsp_task_config(existing)
        if write:
            write_json_config(path, content)
        return install_result(client, path, write_action(write, existed_before), content)

    entry = mcp_server_entry(
        repo_root,
        database_url=database_url,
        database_profile=database_profile,
        privacy_mode=privacy_mode,
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
        include_type=client == "vscode-mcp",
    )
    if client == "vscode-mcp":
        existing = load_json_config(path)
        content = vscode_mcp_config(existing, server_name, entry)
        if write:
            write_json_config(path, content)
        return install_result(client, path, write_action(write, existed_before), content)
    if client == "claude-desktop":
        existing = load_json_config(path)
        content = claude_desktop_config(existing, server_name, entry)
        if write:
            write_json_config(path, content)
        return install_result(client, path, write_action(write, existed_before), content)
    if client == "continue":
        text = render_continue_config(server_name, entry)
        if write:
            if path.exists() and not overwrite:
                return install_result(
                    client,
                    path,
                    "skipped",
                    text,
                    (
                        "Continue YAML merge is conservative; rerun with --overwrite "
                        "or copy the preview."
                    ),
                )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return install_result(client, path, write_action(write, existed_before), text)
    raise ValueError(f"Unsupported install client: {client}")


def write_action(write: bool, existed_before: bool) -> str:
    if not write:
        return "preview"
    return "updated" if existed_before else "created"


def install_result(
    client: str,
    path: Path,
    action: str,
    content: dict[str, Any] | str,
    message: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "client": client,
        "path": str(path),
        "action": action,
        "content": content,
    }
    if message:
        result["message"] = message
    return result


def format_install_result_text(result: dict[str, Any]) -> str:
    lines = [f"Geond install status: {result['status']}", f"Write mode: {result['write']}"]
    for target in result.get("targets", []):
        lines.extend(
            [
                "",
                f"[{target['client']}] {target['action']}: {target['path']}",
            ]
        )
        if target.get("message"):
            lines.append(str(target["message"]))
        content = target.get("content")
        if isinstance(content, str):
            lines.append(content.rstrip())
        elif isinstance(content, dict):
            lines.append(json.dumps(content, ensure_ascii=False, indent=2))
    return "\n".join(lines)
