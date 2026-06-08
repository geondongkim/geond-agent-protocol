from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import geond.code_graph.tree_sitter_indexer as tree_sitter_indexer
from geond.code_graph.tree_sitter_indexer import index_tree_sitter_file, index_tree_sitter_path


def _span(start_line: int, end_line: int) -> SimpleNamespace:
    return SimpleNamespace(start_line=start_line - 1, end_line=end_line - 1)


def _structure_item(
    name: str,
    kind: str,
    *,
    start_line: int,
    end_line: int,
    children: list[SimpleNamespace] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        kind=kind,
        span=_span(start_line, end_line),
        signature=None,
        decorators=[],
        visibility=None,
        children=children or [],
    )


def _process_result(*items: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(imports=[], structure=list(items), diagnostics=[])


def test_tree_sitter_indexer_merges_python_structure_and_call_edges(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "sample.py"
    source.write_text(
        """
class Runner:
    def run(self, value):
        return helper(value)

def helper(value):
    return value.strip()
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        tree_sitter_indexer,
        "process",
        lambda source, config: _process_result(
            _structure_item(
                "Runner",
                "class",
                start_line=1,
                end_line=3,
                children=[
                    _structure_item("run", "method", start_line=2, end_line=3),
                ],
            ),
            _structure_item("helper", "function", start_line=5, end_line=6),
        ),
    )

    indexed = index_tree_sitter_file(source, tmp_path)

    entities = {entity.qualified_name: entity for entity in indexed.entities}
    edge_types = {
        (edge.source_qualified_name, edge.target_qualified_name, edge.edge_type)
        for edge in indexed.edges
    }

    assert indexed.errors == []
    assert entities["sample.Runner"].metadata["indexer"] == "tree-sitter"
    assert entities["sample.Runner.run"].start_line == 2
    assert entities["sample.helper"].signature == "def helper(value):"
    assert ("sample.Runner.run", "sample.helper", "calls") in edge_types


def test_tree_sitter_indexer_keeps_ts_arrow_function_fallback(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "service.ts"
    source.write_text(
        """
import { trim } from "./text";

export function buildAnswer(prompt: string): string {
  return normalize(prompt);
}

const normalize = (value: string) => trim(value);

export class Reporter {
  report(prompt: string) {
    return buildAnswer(prompt);
  }
}
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        tree_sitter_indexer,
        "process",
        lambda source, config: _process_result(
            _structure_item("buildAnswer", "function", start_line=3, end_line=5),
            _structure_item(
                "Reporter",
                "class",
                start_line=9,
                end_line=13,
                children=[
                    _structure_item("report", "method", start_line=10, end_line=12),
                ],
            ),
        ),
    )

    indexed = index_tree_sitter_file(source, tmp_path)

    entities = {entity.qualified_name: entity for entity in indexed.entities}
    edge_types = {
        (edge.source_qualified_name, edge.target_qualified_name, edge.edge_type)
        for edge in indexed.edges
    }

    assert "service.buildAnswer" in entities
    assert entities["service.buildAnswer"].metadata["indexer"] == "tree-sitter"
    assert "service.normalize" in entities
    assert entities["service.normalize"].metadata["tree_sitter_fallback"] is True
    assert entities["service.normalize"].end_line == 7
    assert "service.Reporter.report" in entities
    assert ("service.Reporter.report", "service.buildAnswer", "calls") in edge_types


def test_tree_sitter_path_indexes_mixed_languages(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.ts").write_text("export function b() { return 2; }\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("# ignored\n", encoding="utf-8")

    indexed = index_tree_sitter_path(tmp_path, root_path=tmp_path)

    assert {item.file_path for item in indexed} == {"a.py", "b.ts"}
