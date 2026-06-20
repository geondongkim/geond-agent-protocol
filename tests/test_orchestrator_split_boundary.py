from __future__ import annotations

import ast
import importlib
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).parents[1]
LEGACY_ORCHESTRATOR_MODULES = {
    "orchestrator",
    "orchestrator_action_bundle",
    "orchestrator_action_queue",
    "orchestrator_budget",
    "orchestrator_cli",
    "orchestrator_control",
    "orchestrator_daemon",
    "orchestrator_finalize",
    "orchestrator_graph_review",
    "orchestrator_llm_planner",
    "orchestrator_mcp_bridge",
    "orchestrator_planner",
    "orchestrator_scheduler",
    "orchestrator_spawn",
    "orchestrator_task_planner",
    "orchestrator_worker_review",
}
ALLOWED_PROTOCOL_BRIDGES = {
    (
        "src/geond/mcp_server/server.py",
        "geond.orchestrator_mcp_bridge",
    )
}
PROTOCOL_PATHS = [
    REPO_ROOT / "src" / "geond" / "storage",
    REPO_ROOT / "src" / "geond" / "mcp_server",
    REPO_ROOT / "src" / "geond" / "degraded_ledger.py",
    REPO_ROOT / "src" / "geond" / "task_graph.py",
]
ORCHESTRATOR_PATH = REPO_ROOT / "src" / "geond_orchestrator"


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
                        imported = f"geond.{alias.name}"
                        if alias.name in LEGACY_ORCHESTRATOR_MODULES and not allowed_bridge(
                            path, imported
                        ):
                            violations.append(f"{path.relative_to(REPO_ROOT)} imports {imported}")
                elif imported_private_module(module):
                    if not allowed_bridge(path, module):
                        violations.append(f"{path.relative_to(REPO_ROOT)} imports {module}")

    assert violations == []


def test_orchestrator_package_does_not_import_legacy_wrapper_modules() -> None:
    violations: list[str] = []
    for path in sorted(ORCHESTRATOR_PATH.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if imported_legacy_module(alias.name):
                        violations.append(f"{path.relative_to(REPO_ROOT)} imports {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "geond":
                    for alias in node.names:
                        if alias.name in LEGACY_ORCHESTRATOR_MODULES:
                            violations.append(
                                f"{path.relative_to(REPO_ROOT)} imports geond.{alias.name}"
                            )
                elif imported_legacy_module(module):
                    violations.append(f"{path.relative_to(REPO_ROOT)} imports {module}")

    assert violations == []


def test_legacy_orchestrator_wrappers_alias_canonical_modules() -> None:
    for module_name in sorted(LEGACY_ORCHESTRATOR_MODULES):
        legacy_module = importlib.import_module(f"geond.{module_name}")
        canonical_module = importlib.import_module(f"geond_orchestrator.{module_name}")
        assert legacy_module is canonical_module


def test_orchestrator_console_script_uses_canonical_entrypoint() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = pyproject["project"]["scripts"]
    assert scripts["geond-orchestrator"] == "geond_orchestrator.cli:main"


def protocol_python_files() -> list[Path]:
    files: list[Path] = []
    for path in PROTOCOL_PATHS:
        if path.is_dir():
            files.extend(sorted(path.rglob("*.py")))
        else:
            files.append(path)
    return files


def imported_private_module(module: str) -> bool:
    if module == "geond_orchestrator" or module.startswith("geond_orchestrator."):
        return True
    return imported_legacy_module(module)


def imported_legacy_module(module: str) -> bool:
    return any(
        module == f"geond.{private}" or module.startswith(f"geond.{private}.")
        for private in LEGACY_ORCHESTRATOR_MODULES
    )


def allowed_bridge(path: Path, module: str) -> bool:
    return (path.relative_to(REPO_ROOT).as_posix(), module) in ALLOWED_PROTOCOL_BRIDGES
