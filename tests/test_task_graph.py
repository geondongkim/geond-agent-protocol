from __future__ import annotations

import json

from geond.task_graph import parse_task_graph_text


def test_parse_json_task_graph_normalizes_dependencies() -> None:
    payload = parse_task_graph_text(
        json.dumps(
            {
                "tasks": [
                    {"key": "repro", "title": "Reproduce issue", "priority": 100},
                    {
                        "key": "fix",
                        "title": "Implement fix",
                        "description": "Patch the failing path",
                        "priority": 50,
                        "depends_on": "repro",
                    },
                ]
            }
        )
    )

    assert payload["schema"] == "geond.task_graph_input.v1"
    assert payload["tasks"][1]["depends_on"] == ["repro"]
    assert payload["tasks"][1]["priority"] == 50


def test_parse_markdown_task_graph() -> None:
    payload = parse_task_graph_text(
        """
        - [ ] repro | Reproduce issue | priority=100
        - [ ] fix | Implement fix | priority=50 | depends_on=repro
        """
    )

    assert [task["key"] for task in payload["tasks"]] == ["repro", "fix"]
    assert payload["tasks"][1]["depends_on"] == ["repro"]
