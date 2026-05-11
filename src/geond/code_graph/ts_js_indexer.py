from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from geond.code_graph.python_indexer import CodeEdgeDraft, CodeEntityDraft, IndexedPythonFile

SKIP_DIRS = {
    ".git",
    ".next",
    ".nuxt",
    ".svelte-kit",
    "build",
    "coverage",
    "dist",
    "node_modules",
}
SUPPORTED_SUFFIXES = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}

IMPORT_RE = re.compile(
    r"^\s*import\s+(?P<body>.+?)\s+from\s+['\"](?P<module>[^'\"]+)['\"]|"
    r"^\s*import\s+['\"](?P<side_effect>[^'\"]+)['\"]"
)
CLASS_RE = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?class\s+(?P<name>[A-Za-z_$][\w$]*)")
FUNCTION_RE = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(?P<name>[A-Za-z_$][\w$]*)"
)
CONST_FUNCTION_RE = re.compile(
    r"^\s*(?:export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*"
    r"(?:(?:async\s*)?\([^)]*\)|(?:async\s+)?[A-Za-z_$][\w$]*)"
    r"\s*(?::\s*[^=]+)?=>"
)
METHOD_RE = re.compile(
    r"^\s*(?:public\s+|private\s+|protected\s+|static\s+|async\s+)*"
    r"(?P<name>[A-Za-z_$][\w$]*)\s*\([^)]*\)\s*[:A-Za-z0-9_<>,\s|&[\]?]*\{?\s*$"
)
CALL_RE = re.compile(r"\b(?P<name>[A-Za-z_$][\w$]*)\s*\(")
DECLARATION_KEYWORDS = {"if", "for", "while", "switch", "catch", "function"}


@dataclass(frozen=True)
class IndexedTsJsFile(IndexedPythonFile):
    errors: list[str] = field(default_factory=list)


def index_ts_js_path(path: Path, root_path: Path | None = None) -> list[IndexedPythonFile]:
    resolved_path = path.expanduser().resolve()
    default_root = resolved_path.parent if resolved_path.is_file() else resolved_path
    resolved_root = (root_path or default_root).expanduser().resolve()
    if resolved_path.is_file():
        return [index_ts_js_file(resolved_path, resolved_root)]

    files = [file_path for file_path in iter_ts_js_files(resolved_path)]
    return [index_ts_js_file(file_path, resolved_root) for file_path in files]


def iter_ts_js_files(root_path: Path) -> list[Path]:
    files: list[Path] = []
    for path in root_path.rglob("*"):
        if path.is_file() and path.suffix in SUPPORTED_SUFFIXES and not should_skip(path):
            files.append(path)
    return sorted(files, key=lambda item: item.as_posix())


def should_skip(path: Path) -> bool:
    return any(part in SKIP_DIRS or part.endswith(".egg-info") for part in path.parts)


def index_ts_js_file(file_path: Path, root_path: Path) -> IndexedPythonFile:
    relative_path = file_path.resolve().relative_to(root_path.resolve()).as_posix()
    module_name = module_name_from_path(Path(relative_path))
    language = language_from_suffix(file_path.suffix)
    try:
        source = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        module_entity = CodeEntityDraft(
            kind="module",
            name=module_name.rsplit(".", 1)[-1],
            qualified_name=module_name,
            file_path=relative_path,
            start_line=1,
            end_line=None,
            metadata={"language": language, "index_error": type(exc).__name__},
        )
        return IndexedPythonFile(relative_path, [module_entity], [], [str(exc)])

    visitor = TsJsIndexVisitor(relative_path, module_name, language)
    visitor.index(source)
    visitor.add_resolved_call_edges()
    return IndexedPythonFile(relative_path, visitor.entities, visitor.edges)


def module_name_from_path(relative_path: Path) -> str:
    parts = list(relative_path.with_suffix("").parts)
    if parts[-1] == "index":
        parts = parts[:-1]
    return ".".join(parts) if parts else relative_path.stem


def language_from_suffix(suffix: str) -> str:
    return "typescript" if suffix in {".ts", ".tsx"} else "javascript"


class TsJsIndexVisitor:
    def __init__(self, file_path: str, module_name: str, language: str) -> None:
        self.file_path = file_path
        self.module_name = module_name
        self.language = language
        self.entities: list[CodeEntityDraft] = [
            CodeEntityDraft(
                kind="module",
                name=module_name.rsplit(".", 1)[-1],
                qualified_name=module_name,
                file_path=file_path,
                start_line=1,
                end_line=None,
                metadata={"language": language},
            )
        ]
        self.edges: list[CodeEdgeDraft] = []
        self.name_to_qualified_names: dict[str, list[str]] = {}
        self.calls_by_source: dict[str, list[str]] = {}
        self.class_stack: list[tuple[str, int]] = []
        self.callable_stack: list[tuple[str, int]] = []
        self.brace_depth = 0

    def index(self, source: str) -> None:
        lines = source.splitlines()
        for line_number, line in enumerate(lines, start=1):
            stripped = strip_line_comment(line)
            if not stripped.strip():
                self.update_scope(stripped)
                continue
            if self.add_import(stripped, line_number):
                self.update_scope(stripped)
                continue
            if self.add_class(stripped, line_number):
                self.update_scope(stripped)
                continue
            if self.add_function(stripped, line_number):
                self.update_scope(stripped)
                continue
            if self.add_method(stripped, line_number):
                self.update_scope(stripped)
                continue
            self.add_body_calls(stripped)
            self.update_scope(stripped)

    def update_scope(self, line: str) -> None:
        self.brace_depth += brace_balance(line)
        while self.callable_stack and self.brace_depth <= self.callable_stack[-1][1]:
            self.callable_stack.pop()
        while self.class_stack and self.brace_depth <= self.class_stack[-1][1]:
            self.class_stack.pop()

    def add_import(self, line: str, line_number: int) -> bool:
        match = IMPORT_RE.search(line)
        if not match:
            return False
        module = match.group("module") or match.group("side_effect") or ""
        body = match.group("body") or module.rsplit("/", 1)[-1] or module
        display_name = normalize_import_name(body)
        qualified_name = f"{self.module_name}:import:{line_number}:{display_name}"
        self.add_entity(
            CodeEntityDraft(
                kind="import",
                name=display_name,
                qualified_name=qualified_name,
                file_path=self.file_path,
                start_line=line_number,
                end_line=line_number,
                signature=module,
                metadata={"language": self.language, "imported_name": module},
            )
        )
        self.add_edge(self.module_name, qualified_name, "imports")
        return True

    def add_class(self, line: str, line_number: int) -> bool:
        match = CLASS_RE.search(line)
        if not match:
            return False
        name = match.group("name")
        qualified_name = f"{self.module_name}.{name}"
        self.add_entity(
            CodeEntityDraft(
                kind="class",
                name=name,
                qualified_name=qualified_name,
                file_path=self.file_path,
                start_line=line_number,
                end_line=None,
                metadata={"language": self.language},
            )
        )
        self.add_edge(self.module_name, qualified_name, "contains")
        self.class_stack.append((qualified_name, self.brace_depth))
        return True

    def add_function(self, line: str, line_number: int) -> bool:
        match = FUNCTION_RE.search(line) or CONST_FUNCTION_RE.search(line)
        if not match:
            return False
        name = match.group("name")
        parent = self.class_stack[-1][0] if self.class_stack else self.module_name
        kind = "method" if self.class_stack else "function"
        qualified_name = f"{parent}.{name}"
        self.add_entity(
            CodeEntityDraft(
                kind=kind,
                name=name,
                qualified_name=qualified_name,
                file_path=self.file_path,
                start_line=line_number,
                end_line=None,
                signature=line.strip(),
                metadata={"language": self.language},
            )
        )
        self.add_edge(parent, qualified_name, "contains")
        self.calls_by_source[qualified_name] = find_calls(line, excluded={name})
        if "{" in line:
            self.callable_stack.append((qualified_name, self.brace_depth))
        return True

    def add_method(self, line: str, line_number: int) -> bool:
        if not self.class_stack:
            return False
        match = METHOD_RE.search(line)
        if not match:
            return False
        name = match.group("name")
        if name in DECLARATION_KEYWORDS:
            return False
        parent = self.class_stack[-1][0]
        qualified_name = f"{parent}.{name}"
        self.add_entity(
            CodeEntityDraft(
                kind="method",
                name=name,
                qualified_name=qualified_name,
                file_path=self.file_path,
                start_line=line_number,
                end_line=None,
                signature=line.strip(),
                metadata={"language": self.language},
            )
        )
        self.add_edge(parent, qualified_name, "contains")
        self.calls_by_source[qualified_name] = find_calls(line, excluded={name})
        if "{" in line:
            self.callable_stack.append((qualified_name, self.brace_depth))
        return True

    def add_body_calls(self, line: str) -> None:
        if not self.callable_stack:
            return
        source_qualified_name = self.callable_stack[-1][0]
        calls = find_calls(line)
        if calls:
            self.calls_by_source.setdefault(source_qualified_name, []).extend(calls)

    def add_entity(self, entity: CodeEntityDraft) -> None:
        self.entities.append(entity)
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
        for source_qualified_name, calls in self.calls_by_source.items():
            for call_name in calls:
                targets = self.name_to_qualified_names.get(call_name, [])
                if not targets:
                    continue
                self.add_edge(
                    source_qualified_name,
                    targets[0],
                    "calls",
                    metadata={"call": call_name, "resolution": "same_file_name_match"},
                )


def strip_line_comment(line: str) -> str:
    return line.split("//", 1)[0]


def brace_balance(line: str) -> int:
    return line.count("{") - line.count("}")


def normalize_import_name(body: str) -> str:
    body = body.strip()
    if body.startswith("{") and body.endswith("}"):
        names = [part.strip().split(" as ")[-1].strip() for part in body.strip("{}").split(",")]
        return ",".join(name for name in names if name) or "named"
    if "," in body:
        return body.split(",", 1)[0].strip()
    return body.replace("* as ", "").strip() or "side_effect"


def find_calls(line: str, excluded: set[str] | None = None) -> list[str]:
    excluded = excluded or set()
    calls = []
    for match in CALL_RE.finditer(line):
        name = match.group("name")
        if name not in DECLARATION_KEYWORDS and name not in excluded:
            calls.append(name)
    return sorted(set(calls))
