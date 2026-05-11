from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from geond.config import get_settings
from geond.db import connect
from geond.retrieval.simple import explain_change as explain_change_query
from geond.retrieval.simple import get_symbol_context as get_symbol_context_query
from geond.retrieval.simple import search_dev_memory as search_dev_memory_query
from geond.storage.repository import record_agent_action as record_agent_action_row

mcp = FastMCP("geond-agent-protocol")


@mcp.tool()
def search_dev_memory(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Search shared development memory across imported sessions and messages."""
    with connect(get_settings()) as conn:
        return search_dev_memory_query(conn, query, limit)


@mcp.tool()
def explain_change(file_path: str, limit: int = 10) -> dict[str, Any]:
    """Return stored evidence that may explain why a file changed."""
    with connect(get_settings()) as conn:
        return explain_change_query(conn, file_path, limit)


@mcp.tool()
def get_symbol_context(symbol: str, limit: int = 10) -> list[dict[str, Any]]:
    """Return known code entities matching a symbol name."""
    with connect(get_settings()) as conn:
        return get_symbol_context_query(conn, symbol, limit)


@mcp.tool()
def record_agent_action(
    workspace_id: str,
    agent_name: str,
    action_type: str,
    summary: str,
    intent: str | None = None,
    status: str = "recorded",
) -> dict[str, str]:
    """Record what an agent is doing so other agents can discover it later."""
    with connect(get_settings()) as conn:
        action_id = record_agent_action_row(
            conn=conn,
            workspace_id=workspace_id,
            agent_name=agent_name,
            action_type=action_type,
            summary=summary,
            intent=intent,
            status=status,
        )
    return {"action_id": action_id}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
