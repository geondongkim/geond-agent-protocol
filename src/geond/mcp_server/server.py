from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from geond.config import get_settings
from geond.db import connect
from geond.embeddings import get_embedding_provider
from geond.retrieval.simple import explain_change as explain_change_query
from geond.retrieval.simple import get_changeset_detail as get_changeset_detail_query
from geond.retrieval.simple import get_symbol_context as get_symbol_context_query
from geond.retrieval.simple import hybrid_search_dev_memory as hybrid_search_dev_memory_query
from geond.retrieval.simple import search_dev_memory as search_dev_memory_query
from geond.retrieval.simple import vector_search_dev_memory as vector_search_dev_memory_query
from geond.storage.code_graph import store_lsp_references as store_lsp_references_row
from geond.storage.context_review import review_workspace_context as review_workspace_context_row
from geond.storage.dashboard import get_agent_activity_events as get_agent_activity_events_row
from geond.storage.dashboard import get_dashboard_overview as get_dashboard_overview_row
from geond.storage.mcp_audit import audit_mcp_call
from geond.storage.repository import close_handoff_summary as close_handoff_summary_row
from geond.storage.repository import (
    get_workspace_coordination_policy as get_workspace_coordination_policy_row,
)
from geond.storage.repository import (
    list_active_file_reservations,
    list_active_symbol_reservations,
    upsert_workspace,
)
from geond.storage.repository import list_handoff_summaries as list_handoff_summaries_row
from geond.storage.repository import list_reservation_events as list_reservation_events_row
from geond.storage.repository import (
    list_workspace_aliases as list_workspace_aliases_row,
)
from geond.storage.repository import record_agent_action as record_agent_action_row
from geond.storage.repository import record_changeset as record_changeset_row
from geond.storage.repository import record_handoff_summary as record_handoff_summary_row
from geond.storage.repository import (
    record_workspace_fingerprints as record_workspace_fingerprints_row,
)
from geond.storage.repository import (
    register_workspace_alias as register_workspace_alias_row,
)
from geond.storage.repository import release_reservation as release_reservation_row
from geond.storage.repository import release_symbol_reservation as release_symbol_reservation_row
from geond.storage.repository import renew_reservation as renew_reservation_row
from geond.storage.repository import renew_symbol_reservation as renew_symbol_reservation_row
from geond.storage.repository import reserve_files as reserve_files_row
from geond.storage.repository import reserve_symbols as reserve_symbols_row
from geond.storage.repository import (
    set_workspace_coordination_policy as set_workspace_coordination_policy_row,
)
from geond.storage.repository import suggest_workspace_aliases as suggest_workspace_aliases_row
from geond.storage.resources import (
    get_session_resource,
    get_symbol_resource,
    get_workspace_handoffs,
    get_workspace_lineage,
    get_workspace_reservations,
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
    rerank: str = "none",
    candidate_limit: int | None = None,
) -> list[dict[str, Any]]:
    """Search shared development memory; set rerank to local or api to rerank candidates."""
    settings = get_settings()
    with connect(settings) as conn:

        def run_search() -> list[dict[str, Any]]:
            if mode == "keyword":
                return search_dev_memory_query(
                    conn,
                    query,
                    limit,
                    workspace_uri=workspace_uri,
                    source=source,
                    rerank=rerank,
                    candidate_limit=candidate_limit,
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
                    query=query,
                    rerank=rerank,
                    candidate_limit=candidate_limit,
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
                    rerank=rerank,
                    candidate_limit=candidate_limit,
                )
            raise ValueError("mode must be one of: keyword, vector, hybrid")

        return audit_mcp_call(
            conn,
            item_name="search_dev_memory",
            input_payload={
                "query": query,
                "limit": limit,
                "mode": mode,
                "workspace_uri": workspace_uri,
                "source": source,
                "rerank": rerank,
                "candidate_limit": candidate_limit,
            },
            callback=run_search,
        )


@mcp.tool()
def explain_change(
    file_path: str,
    limit: int = 10,
    include_narrative: bool = False,
) -> dict[str, Any]:
    """Return stored evidence that may explain why a file changed.

    Set `include_narrative=True` to attach a short deterministic narrative
    summary that cites the `geond.evidence.v1` evidence refs.
    """
    settings = get_settings()
    with connect(settings) as conn:
        return explain_change_query(
            conn,
            file_path,
            limit,
            include_narrative=include_narrative,
            settings=settings,
        )


@mcp.tool()
def get_changeset_detail(
    changeset_ref: str,
    include_narrative: bool = False,
) -> dict[str, Any]:
    """Look up a changeset by UUID or git commit (sha or prefix).

    Returns files, touched code entities, and `geond.evidence.v1` evidence
    refs. Set `include_narrative=True` to attach a short narrative summary so
    other agents can read a one-paragraph briefing without paging through the
    raw evidence.
    """
    settings = get_settings()
    with connect(settings) as conn:
        return get_changeset_detail_query(
            conn,
            changeset_ref,
            include_narrative=include_narrative,
            settings=settings,
        )


@mcp.tool()
def get_symbol_context(symbol: str, limit: int = 10) -> list[dict[str, Any]]:
    """Return known code entities matching a symbol name."""
    with connect(get_settings()) as conn:
        return get_symbol_context_query(conn, symbol, limit)


@mcp.tool()
def register_workspace_alias(
    workspace_id_or_uri: str,
    alias_uri: str,
    reason: str = "moved",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Register a moved or renamed workspace URI as an alias for an existing workspace."""
    with connect(get_settings()) as conn:
        return register_workspace_alias_row(
            conn,
            workspace_id_or_uri=workspace_id_or_uri,
            alias_uri=alias_uri,
            reason=reason,
            metadata=metadata,
        )


@mcp.tool()
def list_workspace_aliases(workspace_id_or_uri: str | None = None) -> list[dict[str, Any]]:
    """List workspace aliases, optionally scoped to one workspace id, root URI, or alias URI."""
    with connect(get_settings()) as conn:
        return list_workspace_aliases_row(conn, workspace_id_or_uri)


@mcp.tool()
def record_workspace_fingerprints(
    workspace_id_or_uri: str,
    fingerprints: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Record durable identity fingerprints, such as git remote and first commit."""
    with connect(get_settings()) as conn:
        return record_workspace_fingerprints_row(conn, workspace_id_or_uri, fingerprints)


@mcp.tool()
def suggest_workspace_aliases(
    alias_uri: str,
    fingerprints: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Suggest existing workspaces for a moved folder based on identity fingerprints."""
    with connect(get_settings()) as conn:
        return suggest_workspace_aliases_row(conn, alias_uri, fingerprints)


@mcp.tool()
def get_workspace_coordination_policy(workspace_id_or_uri: str) -> dict[str, Any]:
    """Return the workspace coordination policy, including reservation conflict handling."""
    with connect(get_settings()) as conn:
        return get_workspace_coordination_policy_row(conn, workspace_id_or_uri)


@mcp.tool()
def set_workspace_coordination_policy(
    workspace_id_or_uri: str,
    reservation_conflict_policy: str = "advisory",
) -> dict[str, Any]:
    """Set reservation conflict policy: advisory, strict, or override-with-reason."""
    with connect(get_settings()) as conn:
        return set_workspace_coordination_policy_row(
            conn,
            workspace_id_or_uri,
            reservation_conflict_policy=reservation_conflict_policy,
        )


@mcp.tool()
def record_changeset(
    files: list[dict[str, Any]],
    workspace_id: str | None = None,
    workspace_uri: str | None = None,
    workspace_name: str | None = None,
    git_commit: str | None = None,
    branch: str | None = None,
    intent: str | None = None,
    summary: str = "",
    metadata: dict[str, Any] | None = None,
    session_id: str | None = None,
    session_external_id: str | None = None,
) -> dict[str, Any]:
    """Record a changeset with changed files and optional unified diff patches."""
    if not workspace_id and not workspace_uri:
        raise ValueError("workspace_id or workspace_uri is required")
    with connect(get_settings()) as conn:
        resolved_workspace_id = workspace_id
        if resolved_workspace_id is None:
            resolved_workspace_id = upsert_workspace(
                conn,
                root_uri=str(workspace_uri),
                name=workspace_name or str(workspace_uri),
                metadata={"source": "mcp"},
            )
        return record_changeset_row(
            conn=conn,
            workspace_id=resolved_workspace_id,
            files=files,
            git_commit=git_commit,
            branch=branch,
            intent=intent,
            summary=summary,
            metadata=metadata,
            session_id=session_id,
            session_external_id=session_external_id,
        )


@mcp.tool()
def record_agent_action(
    workspace_id: str,
    agent_name: str,
    action_type: str,
    summary: str,
    intent: str | None = None,
    status: str = "recorded",
    session_id: str | None = None,
    session_external_id: str | None = None,
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
            session_id=session_id,
            session_external_id=session_external_id,
        )
    return {"action_id": action_id}


@mcp.tool()
def reserve_files(
    workspace_id: str,
    agent_name: str,
    file_paths: list[str],
    purpose: str = "",
    ttl_minutes: int | None = 120,
    override_reason: str | None = None,
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
            override_reason=override_reason,
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
def renew_reservation(
    workspace_id: str,
    reservation_id: str | None = None,
    file_path: str | None = None,
    agent_name: str | None = None,
    ttl_minutes: int | None = 120,
) -> dict[str, int]:
    """Renew an active file reservation by id or file path."""
    with connect(get_settings()) as conn:
        renewed = renew_reservation_row(
            conn=conn,
            workspace_id=workspace_id,
            reservation_id=reservation_id,
            file_path=file_path,
            agent_name=agent_name,
            ttl_minutes=ttl_minutes,
        )
    return {"renewed": renewed}


@mcp.tool()
def get_active_reservations(
    workspace_id: str,
    file_paths: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return active file reservations for a workspace."""
    with connect(get_settings()) as conn:
        return list_active_file_reservations(conn, workspace_id, file_paths)


@mcp.tool()
def reserve_symbols(
    workspace_id: str,
    agent_name: str,
    symbols: list[str],
    purpose: str = "",
    ttl_minutes: int | None = 120,
    override_reason: str | None = None,
) -> dict[str, Any]:
    """Reserve symbols so other agents can see symbol-level conflicts."""
    with connect(get_settings()) as conn:
        return reserve_symbols_row(
            conn=conn,
            workspace_id=workspace_id,
            agent_name=agent_name,
            symbols=symbols,
            purpose=purpose,
            ttl_minutes=ttl_minutes,
            override_reason=override_reason,
        )


@mcp.tool()
def release_symbol_reservation(
    workspace_id: str,
    reservation_id: str | None = None,
    symbol: str | None = None,
    agent_name: str | None = None,
) -> dict[str, int]:
    """Release an active symbol reservation by id or symbol name."""
    with connect(get_settings()) as conn:
        released = release_symbol_reservation_row(
            conn=conn,
            workspace_id=workspace_id,
            reservation_id=reservation_id,
            symbol=symbol,
            agent_name=agent_name,
        )
    return {"released": released}


@mcp.tool()
def renew_symbol_reservation(
    workspace_id: str,
    reservation_id: str | None = None,
    symbol: str | None = None,
    agent_name: str | None = None,
    ttl_minutes: int | None = 120,
) -> dict[str, int]:
    """Renew an active symbol reservation by id or symbol name."""
    with connect(get_settings()) as conn:
        renewed = renew_symbol_reservation_row(
            conn=conn,
            workspace_id=workspace_id,
            reservation_id=reservation_id,
            symbol=symbol,
            agent_name=agent_name,
            ttl_minutes=ttl_minutes,
        )
    return {"renewed": renewed}


@mcp.tool()
def get_symbol_conflicts(
    workspace_id: str,
    symbols: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return active symbol reservations that would conflict with new work."""
    with connect(get_settings()) as conn:
        return list_active_symbol_reservations(conn, workspace_id, symbols)


@mcp.tool()
def record_lsp_references(
    workspace_id: str,
    references: list[dict[str, Any]],
    replace: bool = True,
) -> dict[str, Any]:
    """Import LSP-backed reference edges into the code graph."""
    with connect(get_settings()) as conn:
        return store_lsp_references_row(
            conn,
            workspace_id=workspace_id,
            references=references,
            replace=replace,
        )


@mcp.tool()
def review_workspace_context(
    workspace_id_or_uri: str,
    intent: str = "",
    file_paths: list[str] | None = None,
    symbols: list[str] | None = None,
    agent_name: str | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """Compare requested work with Geond reservations, handoffs, and lineage."""
    with connect(get_settings()) as conn:
        return audit_mcp_call(
            conn,
            item_name="review_workspace_context",
            input_payload={
                "workspace_id_or_uri": workspace_id_or_uri,
                "intent": intent,
                "file_paths": file_paths,
                "symbols": symbols,
                "agent_name": agent_name,
                "limit": limit,
            },
            callback=lambda: review_workspace_context_row(
                conn,
                workspace_id_or_uri=workspace_id_or_uri,
                intent=intent,
                file_paths=file_paths,
                symbols=symbols,
                agent_name=agent_name,
                limit=limit,
            ),
        )


@mcp.tool()
def record_handoff_summary(
    workspace_id: str,
    from_agent_name: str,
    summary: str,
    to_agent_name: str | None = None,
    next_steps: list[str] | None = None,
    blocked_on: list[str] | None = None,
    status: str = "open",
    tested_commands: list[str] | None = None,
    remaining_risks: list[str] | None = None,
    next_action: str | None = None,
    template: str = "standard",
) -> dict[str, str]:
    """Record a compact structured handoff summary for the next agent or session."""
    with connect(get_settings()) as conn:
        handoff_id = record_handoff_summary_row(
            conn=conn,
            workspace_id=workspace_id,
            from_agent_name=from_agent_name,
            summary=summary,
            to_agent_name=to_agent_name,
            next_steps=next_steps,
            blocked_on=blocked_on,
            status=status,
            tested_commands=tested_commands,
            remaining_risks=remaining_risks,
            next_action=next_action,
            template=template,
        )
    return {"handoff_id": handoff_id}


@mcp.tool()
def list_handoff_summaries(
    workspace_id_or_uri: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List recorded handoff summaries."""
    with connect(get_settings()) as conn:
        return list_handoff_summaries_row(conn, workspace_id_or_uri, status, limit)


@mcp.tool()
def list_reservation_events(
    workspace_id_or_uri: str | None = None,
    reservation_kind: str | None = None,
    action: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List reservation audit events for created, renewed, released, and expired leases."""
    with connect(get_settings()) as conn:
        return list_reservation_events_row(
            conn,
            workspace_id_or_uri=workspace_id_or_uri,
            reservation_kind=reservation_kind,
            action=action,
            limit=limit,
        )


@mcp.tool()
def close_handoff_summary(handoff_id: str, status: str = "closed") -> dict[str, int]:
    """Close a handoff summary after the next agent has consumed it."""
    with connect(get_settings()) as conn:
        closed = close_handoff_summary_row(conn, handoff_id, status)
    return {"closed": closed}


@mcp.resource("geond://sessions", mime_type="application/json")
def sessions_resource() -> list[dict[str, Any]]:
    """List recent imported sessions."""
    with connect(get_settings()) as conn:
        return list_sessions(conn)


@mcp.resource("geond://sessions/{session_external_id}", mime_type="application/json")
def session_resource(session_external_id: str) -> dict[str, Any]:
    """Read one imported session by row id or external id."""
    with connect(get_settings()) as conn:
        return audit_mcp_call(
            conn,
            item_kind="resource",
            item_name="geond://sessions/{session_external_id}",
            input_payload={"session_external_id": session_external_id},
            callback=lambda: get_session_resource(conn, session_external_id),
        )


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


@mcp.tool()
def get_workspace_lineage_graph(workspace_id: str, limit: int = 100) -> dict[str, Any]:
    """Return a node/edge lineage graph for a workspace."""
    with connect(get_settings()) as conn:
        return get_workspace_lineage(conn, workspace_id, limit=limit)


@mcp.tool()
def get_agent_activity_events(
    workspace_id: str,
    limit: int = 100,
    kind: str | None = None,
    agent: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """Return normalized activity events for dashboard, PM-agent, and orchestrator reads."""
    with connect(get_settings()) as conn:
        return get_agent_activity_events_row(
            conn,
            workspace_id,
            limit=limit,
            event_kind=kind,
            agent_name=agent,
            status=status,
        )


@mcp.tool()
def get_dashboard_overview(workspace_id: str, limit: int = 25) -> dict[str, Any]:
    """Return a read-only dashboard overview for one workspace."""
    with connect(get_settings()) as conn:
        return get_dashboard_overview_row(conn, workspace_id, limit=limit)


@mcp.resource("geond://workspaces/{workspace_id}/lineage", mime_type="application/json")
def workspace_lineage_resource(workspace_id: str) -> dict[str, Any]:
    """Read a workspace lineage graph linking major collaboration artifacts."""
    with connect(get_settings()) as conn:
        return get_workspace_lineage(conn, workspace_id)


@mcp.resource("geond://workspaces/{workspace_id}/activity", mime_type="application/json")
def workspace_activity_resource(workspace_id: str) -> dict[str, Any]:
    """Read normalized dashboard activity events for a workspace."""
    with connect(get_settings()) as conn:
        return get_agent_activity_events_row(conn, workspace_id)


@mcp.resource("geond://workspaces/{workspace_id}/overview", mime_type="application/json")
def workspace_overview_resource(workspace_id: str) -> dict[str, Any]:
    """Read a compact dashboard overview for a workspace."""
    with connect(get_settings()) as conn:
        return get_dashboard_overview_row(conn, workspace_id)


@mcp.resource("geond://workspaces/{workspace_id}/reservations", mime_type="application/json")
def workspace_reservations_resource(workspace_id: str) -> dict[str, Any]:
    """Read active file and symbol reservations for a workspace."""
    with connect(get_settings()) as conn:
        return get_workspace_reservations(conn, workspace_id)


@mcp.resource("geond://workspaces/{workspace_id}/handoffs", mime_type="application/json")
def workspace_handoffs_resource(workspace_id: str) -> dict[str, Any]:
    """Read handoff summaries for a workspace."""
    with connect(get_settings()) as conn:
        return get_workspace_handoffs(conn, workspace_id)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
