from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}


@dataclass(frozen=True)
class CodeEntityDraft:
    kind: str
    name: str
    qualified_name: str
    file_path: str
    start_line: int | None
    end_line: int | None
    signature: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CodeEdgeDraft:
    source_qualified_name: str
    target_qualified_name: str
    edge_type: str
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IndexedPythonFile:
    file_path: str
    entities: list[CodeEntityDraft]
    edges: list[CodeEdgeDraft]
    errors: list[str] = field(default_factory=list)


def index_python_path(path: Path, root_path: Path | None = None) -> list[IndexedPythonFile]:
    resolved_path = path.expanduser().resolve()
    default_root = resolved_path.parent if resolved_path.is_file() else resolved_path
    resolved_root = (root_path or default_root).expanduser().resolve()
    if resolved_path.is_file():
        return [index_python_file(resolved_path, resolved_root)]

    files = [file_path for file_path in iter_python_files(resolved_path)]
    return [index_python_file(file_path, resolved_root) for file_path in files]


def iter_python_files(root_path: Path) -> list[Path]:
    files: list[Path] = []
    for path in root_path.rglob("*.py"):
        if should_skip(path):
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.as_posix())


def should_skip(path: Path) -> bool:
    return any(part in SKIP_DIRS or part.endswith(".egg-info") for part in path.parts)


def index_python_file(file_path: Path, root_path: Path) -> IndexedPythonFile:
    relative_path = file_path.resolve().relative_to(root_path.resolve()).as_posix()
    module_name = module_name_from_path(Path(relative_path))
    try:
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=relative_path)
    except (SyntaxError, UnicodeDecodeError) as exc:
        module_entity = CodeEntityDraft(
            kind="module",
            name=module_name.rsplit(".", 1)[-1],
            qualified_name=module_name,
            file_path=relative_path,
            start_line=1,
            end_line=None,
            metadata={"language": "python", "index_error": type(exc).__name__},
        )
        return IndexedPythonFile(relative_path, [module_entity], [], [str(exc)])

    visitor = PythonIndexVisitor(relative_path, module_name)
    visitor.visit(tree)
    visitor.add_resolved_call_edges()
    return IndexedPythonFile(relative_path, visitor.entities, visitor.edges)


def module_name_from_path(relative_path: Path) -> str:
    parts = list(relative_path.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else relative_path.stem


class PythonIndexVisitor(ast.NodeVisitor):
    def __init__(self, file_path: str, module_name: str) -> None:
        self.file_path = file_path
        self.module_name = module_name
        self.entities: list[CodeEntityDraft] = [
            CodeEntityDraft(
                kind="module",
                name=module_name.rsplit(".", 1)[-1],
                qualified_name=module_name,
                file_path=file_path,
                start_line=1,
                end_line=None,
                metadata={"language": "python"},
            )
        ]
        self.edges: list[CodeEdgeDraft] = []
        self.stack: list[str] = [module_name]
        self.name_to_qualified_names: dict[str, list[str]] = {}
        self.imported_name_by_alias: dict[str, str] = {}
        self.calls_by_source: dict[str, list[str]] = {}

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualified_name = f"{self.stack[-1]}.{node.name}"
        self.add_entity(
            CodeEntityDraft(
                kind="class",
                name=node.name,
                qualified_name=qualified_name,
                file_path=self.file_path,
                start_line=node.lineno,
                end_line=node.end_lineno,
                metadata={
                    "language": "python",
                    "bases": [base for base in (expr_name(base) for base in node.bases) if base],
                },
            )
        )
        self.add_edge(self.stack[-1], qualified_name, "contains")
        self.stack.append(qualified_name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.visit_function(node, is_async=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_function(node, is_async=True)

    def visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, is_async: bool) -> None:
        parent = self.stack[-1]
        qualified_name = f"{parent}.{node.name}"
        kind = "method" if parent != self.module_name else "function"
        self.add_entity(
            CodeEntityDraft(
                kind=kind,
                name=node.name,
                qualified_name=qualified_name,
                file_path=self.file_path,
                start_line=node.lineno,
                end_line=node.end_lineno,
                signature=function_signature(node, is_async=is_async),
                metadata={
                    "language": "python",
                    "async": is_async,
                    "decorators": [
                        name for name in (expr_name(item) for item in node.decorator_list) if name
                    ],
                },
            )
        )
        self.add_edge(parent, qualified_name, "contains")
        calls = sorted(
            {name for name in (expr_name(call.func) for call in find_calls(node)) if name}
        )
        self.calls_by_source[qualified_name] = calls
        self.stack.append(qualified_name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.add_import(alias.name, alias.asname, node.lineno)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module is None:
            return
        for alias in node.names:
            imported_name = f"{'.' * node.level}{node.module}.{alias.name}"
            self.add_import(imported_name, alias.asname, node.lineno)

    def add_import(self, imported_name: str, alias: str | None, line_number: int) -> None:
        display_name = alias or imported_name.rsplit(".", 1)[-1]
        imported_qualified_name = resolve_imported_qualified_name(imported_name, self.module_name)
        self.imported_name_by_alias[display_name] = imported_qualified_name
        qualified_name = f"{self.module_name}:import:{line_number}:{display_name}"
        self.add_entity(
            CodeEntityDraft(
                kind="import",
                name=display_name,
                qualified_name=qualified_name,
                file_path=self.file_path,
                start_line=line_number,
                end_line=line_number,
                signature=imported_name,
                metadata={
                    "language": "python",
                    "imported_name": imported_name,
                    "imported_qualified_name": imported_qualified_name,
                    "alias": alias,
                },
            )
        )
        self.add_edge(self.module_name, qualified_name, "imports")

    def add_entity(self, entity: CodeEntityDraft) -> None:
        self.entities.append(entity)
        if entity.kind in {"class", "function", "method"}:
            self.name_to_qualified_names.setdefault(entity.name, []).append(entity.qualified_name)

    def add_edge(
        self,
        source_qualified_name: str,
        target_qualified_name: str,
        edge_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.edges.append(
            CodeEdgeDraft(
                source_qualified_name=source_qualified_name,
                target_qualified_name=target_qualified_name,
                edge_type=edge_type,
                metadata=metadata or {},
            )
        )

    def add_resolved_call_edges(self) -> None:
        added_edges: set[tuple[str, str, str]] = set()
        for source_qualified_name, calls in self.calls_by_source.items():
            for call_name in calls:
                name = call_name.rsplit(".", 1)[-1]
                targets = self.name_to_qualified_names.get(name, [])
                if targets:
                    self.add_call_edge(
                        source_qualified_name,
                        targets[0],
                        call_name,
                        "same_file_name_match",
                        added_edges,
                    )
                    continue

                imported_target = self.resolve_imported_call(call_name)
                if imported_target:
                    self.add_call_edge(
                        source_qualified_name,
                        imported_target,
                        call_name,
                        "import_qualified_name_match",
                        added_edges,
                    )

    def add_call_edge(
        self,
        source_qualified_name: str,
        target_qualified_name: str,
        call_name: str,
        resolution: str,
        added_edges: set[tuple[str, str, str]],
    ) -> None:
        edge_key = (source_qualified_name, target_qualified_name, "calls")
        if edge_key in added_edges:
            return
        added_edges.add(edge_key)
        self.add_edge(
            source_qualified_name,
            target_qualified_name,
            "calls",
            metadata={"call": call_name, "resolution": resolution},
        )

    def resolve_imported_call(self, call_name: str) -> str | None:
        if call_name in self.imported_name_by_alias:
            return self.imported_name_by_alias[call_name]

        alias, _, member_path = call_name.partition(".")
        if not member_path:
            return None
        imported_base = self.imported_name_by_alias.get(alias)
        if not imported_base:
            return None
        return f"{imported_base}.{member_path}"


def resolve_imported_qualified_name(imported_name: str, module_name: str) -> str:
    if not imported_name.startswith("."):
        return imported_name

    relative_level = len(imported_name) - len(imported_name.lstrip("."))
    suffix = imported_name[relative_level:]
    package_parts = module_name.split(".")[:-1]
    keep_count = max(len(package_parts) - relative_level + 1, 0)
    parts = package_parts[:keep_count]
    if suffix:
        parts.extend(part for part in suffix.split(".") if part)
    return ".".join(parts)


def find_calls(node: ast.AST) -> list[ast.Call]:
    return [child for child in ast.walk(node) if isinstance(child, ast.Call)]


def expr_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = expr_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Call):
        return expr_name(node.func)
    if isinstance(node, ast.Subscript):
        return expr_name(node.value)
    if isinstance(node, ast.Constant):
        return str(node.value)
    return None


def function_signature(node: ast.FunctionDef | ast.AsyncFunctionDef, is_async: bool) -> str:
    prefix = "async def" if is_async else "def"
    args = [arg.arg for arg in node.args.posonlyargs + node.args.args]
    if node.args.vararg:
        args.append(f"*{node.args.vararg.arg}")
    args.extend(arg.arg for arg in node.args.kwonlyargs)
    if node.args.kwarg:
        args.append(f"**{node.args.kwarg.arg}")
    return f"{prefix} {node.name}({', '.join(args)})"
