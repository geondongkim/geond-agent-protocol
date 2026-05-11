from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from tree_sitter_language_pack import ProcessConfig, process

from geond.code_graph.python_indexer import (
    CodeEdgeDraft,
    CodeEntityDraft,
    IndexedPythonFile,
    index_python_file,
    iter_python_files,
)
from geond.code_graph.python_indexer import (
    module_name_from_path as python_module_name_from_path,
)
from geond.code_graph.python_indexer import (
    should_skip as should_skip_python,
)
from geond.code_graph.ts_js_indexer import (
    SUPPORTED_SUFFIXES as TS_JS_SUFFIXES,
)
from geond.code_graph.ts_js_indexer import (
    index_ts_js_file,
    iter_ts_js_files,
    normalize_import_name,
)
from geond.code_graph.ts_js_indexer import (
    module_name_from_path as ts_js_module_name_from_path,
)
from geond.code_graph.ts_js_indexer import (
    should_skip as should_skip_ts_js,
)

SUPPORTED_SUFFIXES = {".py", *TS_JS_SUFFIXES}


def index_tree_sitter_path(path: Path, root_path: Path | None = None) -> list[IndexedPythonFile]:
    resolved_path = path.expanduser().resolve()
    default_root = resolved_path.parent if resolved_path.is_file() else resolved_path
    resolved_root = (root_path or default_root).expanduser().resolve()
    if resolved_path.is_file():
        return [index_tree_sitter_file(resolved_path, resolved_root)]

    files = sorted(iter_supported_files(resolved_path), key=lambda item: item.as_posix())
    return [index_tree_sitter_file(file_path, resolved_root) for file_path in files]


def iter_supported_files(root_path: Path) -> list[Path]:
    python_files = [path for path in iter_python_files(root_path) if path.suffix == ".py"]
    ts_js_files = list(iter_ts_js_files(root_path))
    seen = {path.resolve() for path in python_files}
    return python_files + [path for path in ts_js_files if path.resolve() not in seen]


def index_tree_sitter_file(file_path: Path, root_path: Path) -> IndexedPythonFile:
    relative_path = file_path.resolve().relative_to(root_path.resolve()).as_posix()
    language = language_from_suffix(file_path.suffix)
    if language is None:
        return IndexedPythonFile(
            relative_path,
            [],
            [],
            [f"Unsupported suffix for tree-sitter index: {file_path.suffix}"],
        )

    fallback = fallback_index(file_path, root_path, language)
    try:
        source = file_path.read_text(encoding="utf-8")
        tree_sitter_index = index_tree_sitter_source(
            source=source,
            relative_path=relative_path,
            language=language,
            fallback=fallback,
        )
    except (OSError, UnicodeDecodeError) as exc:
        return append_error(fallback, f"tree-sitter read failed: {exc}")

    return merge_indexed_files(tree_sitter_index, fallback)


def index_tree_sitter_source(
    source: str,
    relative_path: str,
    language: str,
    fallback: IndexedPythonFile,
) -> IndexedPythonFile:
    module_name = module_name_for_relative_path(Path(relative_path), language)
    lines = source.splitlines()
    entities = [
        CodeEntityDraft(
            kind="module",
            name=module_name.rsplit(".", 1)[-1],
            qualified_name=module_name,
            file_path=relative_path,
            start_line=1,
            end_line=len(lines) or 1,
            metadata={"language": normalized_language(language), "indexer": "tree-sitter"},
        )
    ]
    edges: list[CodeEdgeDraft] = []
    errors: list[str] = []

    try:
        result = process(
            source,
            ProcessConfig(
                language=language,
                structure=True,
                imports=True,
                exports=True,
                symbols=True,
                diagnostics=True,
            ),
        )
    except Exception as exc:  # pragma: no cover - native parser errors vary by platform.
        return append_error(fallback, f"tree-sitter parse failed: {exc}")

    for import_info in result.imports:
        entity = import_entity(import_info, module_name, relative_path, language)
        entities.append(entity)
        edges.append(
            CodeEdgeDraft(
                source_qualified_name=module_name,
                target_qualified_name=entity.qualified_name,
                edge_type="imports",
                metadata={"indexer": "tree-sitter"},
            )
        )

    for item in result.structure:
        add_structure_item(
            item=item,
            parent_qualified_name=module_name,
            parent_kind="module",
            relative_path=relative_path,
            language=language,
            lines=lines,
            entities=entities,
            edges=edges,
        )

    for diagnostic in result.diagnostics:
        errors.append(str(diagnostic))

    return IndexedPythonFile(relative_path, entities, edges, errors)


def add_structure_item(
    *,
    item: Any,
    parent_qualified_name: str,
    parent_kind: str,
    relative_path: str,
    language: str,
    lines: list[str],
    entities: list[CodeEntityDraft],
    edges: list[CodeEdgeDraft],
) -> None:
    name = getattr(item, "name", None)
    if not isinstance(name, str) or not name:
        return

    kind = entity_kind(getattr(item, "kind", ""), parent_kind)
    qualified_name = f"{parent_qualified_name}.{name}"
    start_line, end_line = one_based_span(getattr(item, "span", None))
    entity = CodeEntityDraft(
        kind=kind,
        name=name,
        qualified_name=qualified_name,
        file_path=relative_path,
        start_line=start_line,
        end_line=end_line,
        signature=signature_for_item(item, lines, start_line),
        metadata={
            "language": normalized_language(language),
            "indexer": "tree-sitter",
            "tree_sitter_kind": str(getattr(item, "kind", "")),
            "decorators": list(getattr(item, "decorators", []) or []),
            "visibility": getattr(item, "visibility", None),
        },
    )
    entities.append(entity)
    edges.append(
        CodeEdgeDraft(
            source_qualified_name=parent_qualified_name,
            target_qualified_name=qualified_name,
            edge_type="contains",
            metadata={"indexer": "tree-sitter"},
        )
    )

    for child in getattr(item, "children", []) or []:
        add_structure_item(
            item=child,
            parent_qualified_name=qualified_name,
            parent_kind=kind,
            relative_path=relative_path,
            language=language,
            lines=lines,
            entities=entities,
            edges=edges,
        )


def import_entity(
    import_info: Any,
    module_name: str,
    relative_path: str,
    language: str,
) -> CodeEntityDraft:
    start_line, end_line = one_based_span(getattr(import_info, "span", None))
    source = str(getattr(import_info, "source", "") or "")
    display_name = import_display_name(import_info, source)
    qualified_name = f"{module_name}:import:{start_line}:{display_name}"
    return CodeEntityDraft(
        kind="import",
        name=display_name,
        qualified_name=qualified_name,
        file_path=relative_path,
        start_line=start_line,
        end_line=end_line,
        signature=source or None,
        metadata={
            "language": normalized_language(language),
            "indexer": "tree-sitter",
            "source": source,
            "alias": getattr(import_info, "alias", None),
            "items": list(getattr(import_info, "items", []) or []),
        },
    )


def import_display_name(import_info: Any, source: str) -> str:
    alias = getattr(import_info, "alias", None)
    if isinstance(alias, str) and alias:
        return alias
    items = getattr(import_info, "items", None)
    if items:
        return ",".join(str(item) for item in items)
    if source.startswith("import ") and " from " in source:
        body = source.removeprefix("import ").split(" from ", 1)[0].strip()
        return normalize_import_name(body).replace(" ", "_") or "import"
    if source.startswith("from ") and " import " in source:
        return source.rsplit(" import ", 1)[-1].strip().replace(" ", "_") or "import"
    return "import"


def fallback_index(file_path: Path, root_path: Path, language: str) -> IndexedPythonFile:
    if language == "python":
        return index_python_file(file_path, root_path)
    return index_ts_js_file(file_path, root_path)


def merge_indexed_files(
    primary: IndexedPythonFile,
    fallback: IndexedPythonFile,
) -> IndexedPythonFile:
    entities_by_qname: dict[str, CodeEntityDraft] = {
        entity.qualified_name: entity for entity in primary.entities
    }
    for entity in fallback.entities:
        existing = entities_by_qname.get(entity.qualified_name)
        if existing is None:
            entities_by_qname[entity.qualified_name] = replace(
                entity,
                metadata={
                    **entity.metadata,
                    "indexer": entity.metadata.get("indexer", "fallback"),
                    "tree_sitter_fallback": True,
                },
            )
            continue

        entities_by_qname[entity.qualified_name] = replace(
            existing,
            end_line=existing.end_line or entity.end_line,
            signature=existing.signature or entity.signature,
            metadata={
                **entity.metadata,
                **existing.metadata,
                "fallback_indexer": entity.metadata.get("indexer", "ast-or-regex"),
            },
        )

    edges: list[CodeEdgeDraft] = []
    seen_edges: set[tuple[str, str, str]] = set()
    qnames = set(entities_by_qname)
    for edge in [*primary.edges, *fallback.edges]:
        if edge.source_qualified_name not in qnames or edge.target_qualified_name not in qnames:
            continue
        key = (edge.source_qualified_name, edge.target_qualified_name, edge.edge_type)
        if key in seen_edges:
            continue
        seen_edges.add(key)
        edges.append(edge)

    return IndexedPythonFile(
        primary.file_path,
        list(entities_by_qname.values()),
        edges,
        [*primary.errors, *fallback.errors],
    )


def append_error(indexed_file: IndexedPythonFile, error: str) -> IndexedPythonFile:
    return IndexedPythonFile(
        indexed_file.file_path,
        indexed_file.entities,
        indexed_file.edges,
        [*indexed_file.errors, error],
    )


def language_from_suffix(suffix: str) -> str | None:
    match suffix:
        case ".py":
            return "python"
        case ".ts":
            return "typescript"
        case ".tsx":
            return "tsx"
        case ".js" | ".jsx" | ".mjs" | ".cjs":
            return "javascript"
        case _:
            return None


def normalized_language(language: str) -> str:
    if language == "tsx":
        return "typescript"
    return language


def module_name_for_relative_path(relative_path: Path, language: str) -> str:
    if language == "python":
        return python_module_name_from_path(relative_path)
    return ts_js_module_name_from_path(relative_path)


def entity_kind(tree_sitter_kind: object, parent_kind: str) -> str:
    raw = str(tree_sitter_kind).lower()
    if "class" in raw:
        return "class"
    if "interface" in raw:
        return "interface"
    if "method" in raw:
        return "method"
    if "function" in raw:
        return "method" if parent_kind in {"class", "interface"} else "function"
    return raw or "symbol"


def one_based_span(span: Any) -> tuple[int | None, int | None]:
    if span is None:
        return None, None
    start = getattr(span, "start_line", None)
    end = getattr(span, "end_line", None)
    start_line = start + 1 if isinstance(start, int) else None
    end_line = end + 1 if isinstance(end, int) else start_line
    return start_line, end_line


def signature_for_item(item: Any, lines: list[str], start_line: int | None) -> str | None:
    signature = getattr(item, "signature", None)
    if isinstance(signature, str) and signature.strip():
        return signature.strip()
    if start_line is None or start_line < 1 or start_line > len(lines):
        return None
    return lines[start_line - 1].strip()[:240] or None


def should_skip(path: Path) -> bool:
    return should_skip_python(path) or should_skip_ts_js(path)
