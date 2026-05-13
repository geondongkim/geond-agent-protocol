from __future__ import annotations

import json
from pathlib import Path

from geond.install import LSP_TASK_LABEL, install_clients


def test_install_vscode_mcp_preview_uses_safe_local_defaults(tmp_path: Path) -> None:
    result = install_clients(
        ["vscode-mcp"],
        repo_root=tmp_path / "repo",
        workspace_root=tmp_path / "workspace",
    )

    assert result["status"] == "ok"
    assert result["write"] is False
    target = result["targets"][0]
    assert target["action"] == "preview"
    assert target["path"].endswith(".vscode\\mcp.json") or target["path"].endswith(
        ".vscode/mcp.json"
    )
    server = target["content"]["servers"]["geond"]
    assert server["type"] == "stdio"
    assert server["command"] == "uv"
    assert server["env"]["GEOND_DATABASE_PROFILE"] == "local"
    assert server["env"]["GEOND_DATABASE_URL"].startswith("postgresql://geond")
    assert server["env"]["GEOND_PRIVACY_MODE"] == "local-only"
    assert server["env"]["GEOND_EMBEDDING_PROVIDER"] == "none"


def test_install_vscode_mcp_can_use_azure_database_profile(tmp_path: Path) -> None:
    result = install_clients(
        ["vscode-mcp"],
        repo_root=tmp_path / "repo",
        workspace_root=tmp_path / "workspace",
        database_profile="azure",
        database_url="postgresql://example.postgres.database.azure.com/geond?sslmode=require",
    )

    server = result["targets"][0]["content"]["servers"]["geond"]
    assert server["env"]["GEOND_DATABASE_PROFILE"] == "azure"
    assert "GEOND_DATABASE_URL" not in server["env"]
    assert server["env"]["AZURE_GEOND_DATABASE_URL"].startswith("postgresql://example")


def test_install_vscode_mcp_merges_existing_json(tmp_path: Path) -> None:
    config_path = tmp_path / ".vscode" / "mcp.json"
    config_path.parent.mkdir()
    config_path.write_text(
        json.dumps({"servers": {"other": {"command": "other-command"}}}),
        encoding="utf-8",
    )

    result = install_clients(
        ["vscode-mcp"],
        repo_root=tmp_path / "repo",
        workspace_root=tmp_path,
        write=True,
    )

    assert result["targets"][0]["action"] == "updated"
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["servers"]["other"]["command"] == "other-command"
    assert saved["servers"]["geond"]["args"][-1] == "geond-mcp"


def test_install_vscode_lsp_task_merges_by_label_and_input_id(tmp_path: Path) -> None:
    config_path = tmp_path / ".vscode" / "tasks.json"
    config_path.parent.mkdir()
    config_path.write_text(
        json.dumps(
            {
                "version": "2.0.0",
                "tasks": [{"label": "Existing", "type": "shell", "command": "true"}],
                "inputs": [{"id": "targetLine", "type": "promptString", "default": "1"}],
            }
        ),
        encoding="utf-8",
    )

    install_clients(
        ["vscode-lsp-task"],
        repo_root=tmp_path / "repo",
        workspace_root=tmp_path,
        write=True,
    )

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    labels = [task["label"] for task in saved["tasks"]]
    input_ids = [item["id"] for item in saved["inputs"]]
    assert labels == ["Existing", LSP_TASK_LABEL]
    assert input_ids.count("targetLine") == 1
    assert "serverProfile" in input_ids


def test_install_continue_refuses_existing_yaml_without_overwrite(tmp_path: Path) -> None:
    config_path = tmp_path / "continue.yaml"
    config_path.write_text("mcpServers: []\n", encoding="utf-8")

    result = install_clients(
        ["continue"],
        repo_root=tmp_path / "repo",
        workspace_root=tmp_path,
        config_path=config_path,
        write=True,
    )

    assert result["status"] == "warning"
    assert result["targets"][0]["action"] == "skipped"
    assert config_path.read_text(encoding="utf-8") == "mcpServers: []\n"


def test_install_all_expands_clients_in_stable_order(tmp_path: Path) -> None:
    result = install_clients(
        ["all"],
        repo_root=tmp_path / "repo",
        workspace_root=tmp_path / "workspace",
    )

    assert [target["client"] for target in result["targets"]] == [
        "vscode-mcp",
        "claude-desktop",
        "continue",
        "vscode-lsp-task",
    ]
