from __future__ import annotations

import json
from pathlib import Path

from geond.code_graph.lsp_references import normalize_lsp_references


def test_normalize_vscode_reference_locations_fixture() -> None:
    payload = json.loads(Path("examples/lsp_references/vscode_locations.json").read_text())

    references = normalize_lsp_references(payload)

    assert references == [
        {
            "target_qualified_name": "service.build_answer",
            "provider": "vscode.executeReferenceProvider",
            "reference": {
                "file_path": "service.py",
                "start_line": 4,
                "start_character": 11,
                "end_line": 4,
                "end_character": 23,
            },
            "metadata": {
                "lsp": {
                    "uri": "file:///C:/workspace/python_service/service.py",
                    "range": {
                        "start": {"line": 3, "character": 11},
                        "end": {"line": 3, "character": 23},
                    },
                }
            },
        }
    ]


def test_existing_import_reference_schema_passes_through() -> None:
    payload = {
        "references": [
            {
                "target_qualified_name": "service.build_answer",
                "reference": {"file_path": "service.py", "start_line": 4},
            }
        ]
    }

    assert normalize_lsp_references(payload) == payload["references"]


def test_vscode_uri_object_and_cli_overrides() -> None:
    payload = [
        {
            "uri": {
                "external": "file:///C:/workspace/python_service/service.py",
                "fsPath": "C:\\workspace\\python_service\\service.py",
            },
            "range": {
                "start": {"line": "7", "character": "2"},
                "end": {"line": 7, "character": 16},
            },
        }
    ]

    references = normalize_lsp_references(
        payload,
        workspace_root="C:/workspace/python_service",
        target_qualified_name="service.format_answer",
        provider="manual-vscode-export",
    )

    assert references[0]["target_qualified_name"] == "service.format_answer"
    assert references[0]["provider"] == "manual-vscode-export"
    assert references[0]["reference"] == {
        "file_path": "service.py",
        "start_line": 8,
        "start_character": 2,
        "end_line": 8,
        "end_character": 16,
    }
