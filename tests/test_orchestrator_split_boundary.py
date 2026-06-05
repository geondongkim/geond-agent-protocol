from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).parents[1]
PRIVATE_CONTROL_MODULES = {
    "orchestrator_control",
    "orchestrator_llm_planner",
    "orchestrator_spawn",
    "orchestrator_action_bundle",
    "orchestrator_graph_review",
    "orchestrator_action_queue",
    "orchestrator_scheduler",
}
PROTOCOL_PATHS = [
    REPO_ROOT / "src" / "geond" / "storage",
    REPO_ROOT / "src" / "geond" / "mcp_server",
    REPO_ROOT / "src" / "geond" / "degraded_ledger.py",
    REPO_ROOT / "src" / "geond" / "task_graph.py",
]


def test_protocol_layers_do_not_directly_import_private_orchestrator_modules() -> None:
    violations: list[str] = []
    for path in protocol_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if imported_private_module(alias.name):
                        violations.append(f"{path.relative_to(REPO_ROOT)} imports {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "geond":
                    for alias in node.names:
                        if alias.name in PRIVATE_CONTROL_MODULES:
                            violations.append(
                                f"{path.relative_to(REPO_ROOT)} imports geond.{alias.name}"
                            )
                elif imported_private_module(module):
                    violations.append(f"{path.relative_to(REPO_ROOT)} imports {module}")

    assert violations == []


def protocol_python_files() -> list[Path]:
    files: list[Path] = []
    for path in PROTOCOL_PATHS:
        if path.is_dir():
            files.extend(sorted(path.rglob("*.py")))
        else:
            files.append(path)
    return files


def imported_private_module(module: str) -> bool:
    return any(
        module == f"geond.{private}" or module.startswith(f"geond.{private}.")
        for private in PRIVATE_CONTROL_MODULES
    )
