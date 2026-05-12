from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
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
EXPORT_FROM_RE = re.compile(
    r"^\s*export\s+(?:type\s+)?(?P<body>\*|\{[^}]*\})\s+from\s+"
    r"['\"](?P<module>[^'\"]+)['\"]"
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
MEMBER_CALL_RE = re.compile(
    r"(?<![A-Za-z0-9_$])(?P<name>[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)?)\s*\("
)
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
    visitor.finalize_spans(source.splitlines())
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
        self.imported_name_by_alias: dict[str, str] = {}
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
            if self.add_reexport(stripped, line_number):
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
        imported_bindings = parse_import_bindings(body, module, self.module_name)
        self.imported_name_by_alias.update(imported_bindings)
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
                metadata={
                    "language": self.language,
                    "imported_name": module,
                    "imported_bindings": imported_bindings,
                },
            )
        )
        self.add_edge(self.module_name, qualified_name, "imports")
        return True

    def add_reexport(self, line: str, line_number: int) -> bool:
        match = EXPORT_FROM_RE.search(line)
        if not match:
            return False
        module = match.group("module")
        body = match.group("body")
        reexported_bindings, reexported_modules = parse_reexport_bindings(
            body,
            module,
            self.module_name,
        )
        display_name = normalize_reexport_name(body)
        qualified_name = f"{self.module_name}:reexport:{line_number}:{display_name}"
        self.add_entity(
            CodeEntityDraft(
                kind="reexport",
                name=display_name,
                qualified_name=qualified_name,
                file_path=self.file_path,
                start_line=line_number,
                end_line=line_number,
                signature=module,
                metadata={
                    "language": self.language,
                    "imported_name": module,
                    "reexported_bindings": reexported_bindings,
                    "reexported_modules": reexported_modules,
                },
            )
        )
        self.add_edge(self.module_name, qualified_name, "reexports")
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
                metadata={
                    "language": self.language,
                    "default_export": is_default_export(line),
                },
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
                metadata={
                    "language": self.language,
                    "default_export": is_default_export(line),
                },
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

    def finalize_spans(self, lines: list[str]) -> None:
        line_count = len(lines) or 1
        finalized: list[CodeEntityDraft] = []
        for entity in self.entities:
            if entity.kind == "module":
                finalized.append(replace(entity, end_line=line_count))
                continue
            if entity.end_line is not None or entity.start_line is None:
                finalized.append(entity)
                continue
            finalized.append(
                replace(entity, end_line=infer_block_end_line(lines, entity.start_line))
            )
        self.entities = finalized


def strip_line_comment(line: str) -> str:
    return line.split("//", 1)[0]


def is_default_export(line: str) -> bool:
    return bool(re.match(r"^\s*export\s+default\b", line))


def brace_balance(line: str) -> int:
    return line.count("{") - line.count("}")


def infer_block_end_line(lines: list[str], start_line: int) -> int:
    if start_line < 1 or start_line > len(lines):
        return start_line

    balance = 0
    seen_open = False
    for index in range(start_line - 1, len(lines)):
        line = strip_line_comment(lines[index])
        if "{" in line:
            seen_open = True
        balance += brace_balance(line)
        if seen_open and balance <= 0:
            return index + 1
        if not seen_open and index == start_line - 1:
            return start_line
    return len(lines)


def normalize_import_name(body: str) -> str:
    body = body.strip()
    if body.startswith("{") and body.endswith("}"):
        names = [part.strip().split(" as ")[-1].strip() for part in body.strip("{}").split(",")]
        return ",".join(name for name in names if name) or "named"
    if "," in body:
        return body.split(",", 1)[0].strip()
    return body.replace("* as ", "").strip() or "side_effect"


def normalize_reexport_name(body: str) -> str:
    body = body.strip()
    if body == "*":
        return "*"
    names = []
    for part in body.strip("{}").split(","):
        name = part.strip()
        if not name:
            continue
        if name.startswith("type "):
            name = name.removeprefix("type ").strip()
        names.append(name.split(" as ")[-1].strip())
    return ",".join(name for name in names if name) or "reexport"


def resolve_imported_module_name(imported_module: str, module_name: str) -> str:
    if not imported_module.startswith("."):
        return imported_module.replace("/", ".")

    parts = module_name.split(".")[:-1]
    for part in imported_module.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.extend(segment for segment in part.split(".") if segment)
    if len(parts) > 1 and parts[-1] == "index":
        parts.pop()
    return ".".join(parts)


def parse_import_bindings(body: str, imported_module: str, module_name: str) -> dict[str, str]:
    module_target = resolve_imported_module_name(imported_module, module_name)
    bindings: dict[str, str] = {}
    body = body.strip()
    if body.startswith("type "):
        body = body.removeprefix("type ").strip()

    namespace_match = re.search(r"\*\s+as\s+(?P<alias>[A-Za-z_$][\w$]*)", body)
    if namespace_match:
        bindings[namespace_match.group("alias")] = module_target

    named_match = re.search(r"\{(?P<named>[^}]*)\}", body)
    if named_match:
        for raw_part in named_match.group("named").split(","):
            part = raw_part.strip()
            if not part:
                continue
            if part.startswith("type "):
                part = part.removeprefix("type ").strip()
            if " as " in part:
                imported, alias = [item.strip() for item in part.split(" as ", 1)]
            else:
                imported = alias = part
            if alias:
                bindings[alias] = f"{module_target}.{imported}"

    default_part = body.split(",", 1)[0].strip()
    if default_part and not default_part.startswith(("{", "*")):
        bindings.setdefault(default_part, f"{module_target}.default")
    return bindings


def parse_reexport_bindings(
    body: str,
    imported_module: str,
    module_name: str,
) -> tuple[dict[str, str], list[str]]:
    module_target = resolve_imported_module_name(imported_module, module_name)
    body = body.strip()
    if body == "*":
        return {}, [module_target]

    bindings: dict[str, str] = {}
    for raw_part in body.strip("{}").split(","):
        part = raw_part.strip()
        if not part:
            continue
        if part.startswith("type "):
            part = part.removeprefix("type ").strip()
        if " as " in part:
            imported, exported = [item.strip() for item in part.split(" as ", 1)]
        else:
            imported = exported = part
        if not exported:
            continue
        target_name = "default" if imported == "default" else imported
        bindings[f"{module_name}.{exported}"] = f"{module_target}.{target_name}"
    return bindings, []


def find_calls(line: str, excluded: set[str] | None = None) -> list[str]:
    excluded = excluded or set()
    calls = []
    for match in MEMBER_CALL_RE.finditer(line):
        name = match.group("name")
        base_name = name.rsplit(".", 1)[-1]
        if base_name not in DECLARATION_KEYWORDS and base_name not in excluded:
            calls.append(name)
    return sorted(set(calls))
