from __future__ import annotations

import json
import tempfile
from functools import partial
from pathlib import Path
from typing import Any

import anyio
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def _parse_json_text(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _extract_text_payloads(items: list[Any]) -> list[Any]:
    payloads: list[Any] = []
    for item in items:
        text = getattr(item, "text", None)
        if isinstance(text, str):
            payloads.append(_parse_json_text(text))
    return payloads


def _status_from_checks(checks: list[dict[str, Any]]) -> str:
    if any(check["status"] == "error" for check in checks):
        return "error"
    if any(check["status"] == "warning" for check in checks):
        return "warning"
    return "ok"


async def _run_stdio_smoke(
    *,
    command: str,
    args: list[str],
    cwd: Path,
    query: str,
    workspace_uri: str | None,
    limit: int,
    required_tools: list[str],
    allow_empty_search: bool,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    params = StdioServerParameters(command=command, args=args, cwd=cwd)
    with tempfile.TemporaryFile("w+t", encoding="utf-8") as server_log:
        async with stdio_client(params, errlog=server_log) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                initialize_result = await session.initialize()
                server_info = initialize_result.serverInfo
                checks.append(
                    {
                        "name": "initialize",
                        "status": "ok",
                        "message": f"Connected to {server_info.name}.",
                        "metadata": {
                            "server_name": server_info.name,
                            "server_version": server_info.version,
                        },
                    }
                )

                tools_result = await session.list_tools()
                tool_names = sorted(tool.name for tool in tools_result.tools)
                missing_tools = [name for name in required_tools if name not in tool_names]
                checks.append(
                    {
                        "name": "list_tools",
                        "status": "error" if missing_tools else "ok",
                        "message": (
                            f"Registered {len(tool_names)} tools."
                            if not missing_tools
                            else f"Missing required tools: {', '.join(missing_tools)}."
                        ),
                        "metadata": {
                            "tool_count": len(tool_names),
                            "required_tools": required_tools,
                            "missing_tools": missing_tools,
                        },
                    }
                )

                resources_result = await session.list_resources()
                resource_uris = sorted(str(resource.uri) for resource in resources_result.resources)
                templates_result = await session.list_resource_templates()
                template_uris = sorted(
                    str(getattr(template, "uriTemplate", getattr(template, "uri_template", "")))
                    for template in templates_result.resourceTemplates
                )
                checks.append(
                    {
                        "name": "list_resources",
                        "status": "ok",
                        "message": (
                            f"Registered {len(resource_uris)} resources and "
                            f"{len(template_uris)} resource templates."
                        ),
                        "metadata": {
                            "resource_count": len(resource_uris),
                            "resource_template_count": len(template_uris),
                        },
                    }
                )

                sessions_resource = await session.read_resource("geond://sessions")
                session_payloads = _extract_text_payloads(list(sessions_resource.contents))
                session_count = (
                    len(session_payloads[0])
                    if session_payloads and isinstance(session_payloads[0], list)
                    else len(session_payloads)
                )
                checks.append(
                    {
                        "name": "read_sessions_resource",
                        "status": "ok",
                        "message": f"Read geond://sessions with {session_count} sessions.",
                        "metadata": {"session_count": session_count},
                    }
                )

                tool_args: dict[str, Any] = {
                    "query": query,
                    "mode": "keyword",
                    "limit": limit,
                }
                if workspace_uri:
                    tool_args["workspace_uri"] = workspace_uri
                search_result = await session.call_tool("search_dev_memory", tool_args)
                search_payloads = _extract_text_payloads(list(search_result.content))
                search_count = len(search_payloads)
                search_status = "ok" if search_count or allow_empty_search else "warning"
                empty_message = (
                    "search_dev_memory returned no results; allowed by --allow-empty-search."
                    if allow_empty_search
                    else (
                        "search_dev_memory returned no results; run seed-sample, choose a "
                        "query known to exist, or use --allow-empty-search for a structural smoke."
                    )
                )
                checks.append(
                    {
                        "name": "call_search_dev_memory",
                        "status": search_status,
                        "message": (
                            f"search_dev_memory returned {search_count} results."
                            if search_count
                            else empty_message
                        ),
                        "metadata": {
                            "query": query,
                            "workspace_uri": workspace_uri,
                            "result_count": search_count,
                            "allow_empty_search": allow_empty_search,
                        },
                    }
                )
        server_log.seek(0)
        server_log_text = server_log.read().strip()

    return {
        "status": _status_from_checks(checks),
        "server_command": command,
        "server_args": args,
        "workspace_root": str(cwd),
        "checks": checks,
        "server_log": server_log_text,
    }


def run_stdio_smoke(
    *,
    command: str = "uv",
    args: list[str] | None = None,
    cwd: Path | None = None,
    query: str = "app_context",
    workspace_uri: str | None = "file:///sample/geond",
    limit: int = 3,
    required_tools: list[str] | None = None,
    allow_empty_search: bool = False,
) -> dict[str, Any]:
    root = cwd or Path.cwd()
    server_args = args if args is not None else ["--directory", str(root), "run", "geond-mcp"]
    tools = required_tools or ["search_dev_memory", "explain_change", "get_symbol_context"]
    smoke = partial(
        _run_stdio_smoke,
        command=command,
        args=server_args,
        cwd=root,
        query=query,
        workspace_uri=workspace_uri,
        limit=limit,
        required_tools=tools,
        allow_empty_search=allow_empty_search,
    )
    return anyio.run(smoke)


def format_smoke_report(report: dict[str, Any]) -> str:
    lines = [f"MCP smoke: {report['status']}", ""]
    for check in report["checks"]:
        lines.append(f"[{check['status'].upper()}] {check['name']}: {check['message']}")
    if report.get("server_log") and report["status"] != "ok":
        lines.extend(["", "Server log:", str(report["server_log"])])
    return "\n".join(lines)
