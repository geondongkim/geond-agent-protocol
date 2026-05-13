from __future__ import annotations

from geond.mcp_smoke import _status_from_checks, format_smoke_report


def test_format_smoke_report_includes_status_and_checks() -> None:
    report = {
        "status": "warning",
        "checks": [
            {
                "name": "initialize",
                "status": "ok",
                "message": "Connected to geond-agent-protocol.",
            },
            {
                "name": "call_search_dev_memory",
                "status": "warning",
                "message": "search_dev_memory returned no results.",
            },
        ],
        "server_log": "",
    }

    output = format_smoke_report(report)

    assert "MCP smoke: warning" in output
    assert "[OK] initialize" in output
    assert "[WARNING] call_search_dev_memory" in output


def test_status_from_checks_allows_empty_search_when_marked_ok() -> None:
    assert (
        _status_from_checks(
            [
                {"name": "initialize", "status": "ok"},
                {"name": "call_search_dev_memory", "status": "ok"},
            ]
        )
        == "ok"
    )
