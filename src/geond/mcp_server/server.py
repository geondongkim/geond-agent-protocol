from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from geond.config import get_settings
from geond.db import connect
from geond.embeddings import get_embedding_provider
from geond.retrieval.simple import explain_change as explain_change_query
from geond.retrieval.simple import get_symbol_context as get_symbol_context_query
from geond.retrieval.simple import hybrid_search_dev_memory as hybrid_search_dev_memory_query
from geond.retrieval.simple import search_dev_memory as search_dev_memory_query
from geond.retrieval.simple import vector_search_dev_memory as vector_search_dev_memory_query
from geond.storage.repository import list_active_file_reservations
from geond.storage.repository import record_agent_action as record_agent_action_row
from geond.storage.repository import release_reservation as release_reservation_row
from geond.storage.repository import reserve_files as reserve_files_row
from geond.storage.resources import (
    get_session_resource,
    get_symbol_resource,
    get_workspace_timeline,
    list_changesets,
    list_sessions,
)

mcp = FastMCP("geond-agent-protocol")


@mcp.tool()
def search_dev_memory(
    query: str,
    limit: int = 10,
    mode: str = "hybrid",
    workspace_uri: str | None = None,
    source: str | None = None,
) -> list[dict[str, Any]]:
    """Search shared development memory across imported sessions and messages."""
    settings = get_settings()
    with connect(settings) as conn:
        if mode == "keyword":
            return search_dev_memory_query(
                conn,
                query,
                limit,
                workspace_uri=workspace_uri,
                source=source,
            )

        provider = get_embedding_provider(settings)
        query_vector = provider.embed([query])[0]
        if mode == "vector":
            return vector_search_dev_memory_query(
                conn,
                query_vector=query_vector,
                model=provider.model,
                limit=limit,
                workspace_uri=workspace_uri,
                source=source,
            )
        if mode == "hybrid":
            return hybrid_search_dev_memory_query(
                conn,
                query=query,
                query_vector=query_vector,
                model=provider.model,
                limit=limit,
                workspace_uri=workspace_uri,
                source=source,
            )
        raise ValueError("mode must be one of: keyword, vector, hybrid")


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


@mcp.tool()
def reserve_files(
    workspace_id: str,
    agent_name: str,
    file_paths: list[str],
    purpose: str = "",
    ttl_minutes: int | None = 120,
) -> dict[str, Any]:
    """Reserve files so other agents can see active work and conflicts."""
    with connect(get_settings()) as conn:
        return reserve_files_row(
            conn=conn,
            workspace_id=workspace_id,
            agent_name=agent_name,
            file_paths=file_paths,
            purpose=purpose,
            ttl_minutes=ttl_minutes,
        )


@mcp.tool()
def release_reservation(
    workspace_id: str,
    reservation_id: str | None = None,
    file_path: str | None = None,
    agent_name: str | None = None,
) -> dict[str, int]:
    """Release an active file reservation by id or file path."""
    with connect(get_settings()) as conn:
        released = release_reservation_row(
            conn=conn,
            workspace_id=workspace_id,
            reservation_id=reservation_id,
            file_path=file_path,
            agent_name=agent_name,
        )
    return {"released": released}


@mcp.tool()
def get_active_reservations(
    workspace_id: str,
    file_paths: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return active file reservations for a workspace."""
    with connect(get_settings()) as conn:
        return list_active_file_reservations(conn, workspace_id, file_paths)


@mcp.resource("geond://sessions", mime_type="application/json")
def sessions_resource() -> list[dict[str, Any]]:
    """List recent imported sessions."""
    with connect(get_settings()) as conn:
        return list_sessions(conn)


@mcp.resource("geond://sessions/{session_external_id}", mime_type="application/json")
def session_resource(session_external_id: str) -> dict[str, Any]:
    """Read one imported session by row id or external id."""
    with connect(get_settings()) as conn:
        return get_session_resource(conn, session_external_id)


@mcp.resource("geond://symbols/{symbol}", mime_type="application/json")
def symbol_resource(symbol: str) -> dict[str, Any]:
    """Read code graph entities matching a symbol."""
    with connect(get_settings()) as conn:
        return get_symbol_resource(conn, symbol)


@mcp.resource("geond://changesets", mime_type="application/json")
def changesets_resource() -> list[dict[str, Any]]:
    """List recent changesets."""
    with connect(get_settings()) as conn:
        return list_changesets(conn)


@mcp.resource("geond://workspaces/{workspace_id}/timeline", mime_type="application/json")
def workspace_timeline_resource(workspace_id: str) -> dict[str, Any]:
    """Read a workspace timeline of sessions, reservations, and agent actions."""
    with connect(get_settings()) as conn:
        return get_workspace_timeline(conn, workspace_id)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
