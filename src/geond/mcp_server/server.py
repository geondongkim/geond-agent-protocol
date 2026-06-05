from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from geond import orchestrator_mcp_bridge, orchestrator_planner, orchestrator_task_planner
from geond.config import get_settings
from geond.db import connect
from geond.embeddings import get_embedding_provider
from geond.retrieval.simple import explain_change as explain_change_query
from geond.retrieval.simple import get_changeset_detail as get_changeset_detail_query
from geond.retrieval.simple import get_symbol_context as get_symbol_context_query
from geond.retrieval.simple import hybrid_search_dev_memory as hybrid_search_dev_memory_query
from geond.retrieval.simple import search_dev_memory as search_dev_memory_query
from geond.retrieval.simple import vector_search_dev_memory as vector_search_dev_memory_query
from geond.storage import orchestration as orchestration_store
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


def _installed_version() -> str:
    try:
        return package_version("geond-agent-protocol")
    except PackageNotFoundError:
        return "0.1.1"


TOOL_DESCRIPTION_HEADINGS = (
    "When to use:",
    "Inputs:",
    "Side effects:",
    "Output:",
    "Failure modes:",
)


TOOL_METADATA: dict[str, dict[str, Any]] = {
    "get_geond_server_info": {
        "title": "Get Geond server info",
        "description": """
Purpose: Return a safe, read-only summary of the Geond Agent Protocol MCP server.
When to use: Call this first when an MCP host, Glama browser session, or new agent
needs to understand what Geond does before connecting it to PostgreSQL. Inputs:
none. Side effects: none; this tool never opens a database connection and does not
read local transcripts. Output: server purpose, version, environment variables,
tool groups, setup hints, and example workflows. Failure modes: only package
metadata lookup fallback is expected, in which case the package fallback version
is returned.
""".strip(),
        "params": {},
    },
    "search_dev_memory": {
        "title": "Search development memory",
        "description": """
Purpose: Search imported agent transcripts, changesets, and shared development
memory for evidence relevant to a question. When to use: use this before editing,
reviewing, or explaining repo behavior so the agent can reuse prior context.
Inputs: query is the natural-language search text; mode selects keyword, vector,
or hybrid retrieval; filters scope the search by workspace or source; rerank can
improve ranking. Side effects: records an MCP audit event. Output: compact search
hits with snippets, scores, sources, and evidence references. Failure modes:
invalid mode raises an error; vector or hybrid search requires an embedding
provider and database connectivity.
""".strip(),
        "params": {
            "query": ("Natural-language question or keywords to search for in shared memory."),
            "limit": "Maximum number of search results to return.",
            "mode": (
                "Retrieval mode: keyword for lexical search, vector for embeddings, "
                "hybrid for both."
            ),
            "workspace_uri": ("Optional workspace root URI used to restrict results to one repo."),
            "source": (
                "Optional imported source filter such as codex, vscode, claude-code, or manus."
            ),
            "rerank": "Reranking strategy: none, local, or api depending on configured providers.",
            "candidate_limit": (
                "Optional number of pre-rerank candidates to retrieve before trimming to limit."
            ),
        },
    },
    "explain_change": {
        "title": "Explain file change",
        "description": """
Purpose: Explain why a file may have changed using stored changesets, code graph
entries, snapshots, and related messages. When to use: call during code review,
bug triage, or handoff recovery when a file path needs historical context.
Inputs: file_path identifies the repo-relative file; limit caps evidence volume;
include_narrative adds a deterministic cited summary. Side effects: none beyond
database reads. Output: changesets, touched entities, snapshots, related messages,
and optional geond.evidence.v1 narrative citations. Failure modes: returns sparse
evidence when the file was not indexed or imported.
""".strip(),
        "params": {
            "file_path": "Repo-relative path to the file whose history should be explained.",
            "limit": "Maximum number of evidence rows to include per evidence category.",
            "include_narrative": "Whether to include a concise cited narrative summary.",
        },
    },
    "get_changeset_detail": {
        "title": "Get changeset detail",
        "description": """
Purpose: Retrieve full stored detail for one changeset. When to use: call after a
search or file explanation returns a changeset id or git commit that needs closer
inspection. Inputs: changeset_ref accepts a UUID, full git SHA, or unambiguous SHA
prefix; include_narrative controls cited prose. Side effects: none beyond database
reads. Output: files, touched code entities, evidence references, ambiguity status,
and optional narrative. Failure modes: returns found=false for missing refs or
ambiguous=true when a prefix matches multiple changesets.
""".strip(),
        "params": {
            "changeset_ref": "Changeset UUID, full git commit SHA, or unambiguous commit prefix.",
            "include_narrative": "Whether to include a concise cited narrative summary.",
        },
    },
    "get_symbol_context": {
        "title": "Get symbol context",
        "description": """
Purpose: Find known code graph entities that match a symbol name. When to use:
call before modifying a function, class, method, or variable so the agent can see
definitions and related changesets. Inputs: symbol is the name to match; limit
caps returned entities. Side effects: none beyond database reads. Output: matching
entities with file locations, workspace data, related changesets, and evidence
refs. Failure modes: returns an empty list when the code graph has not indexed
the symbol.
""".strip(),
        "params": {
            "symbol": "Function, class, method, or other code symbol name to look up.",
            "limit": "Maximum number of matching code entities to return.",
        },
    },
    "register_workspace_alias": {
        "title": "Register workspace alias",
        "description": """
Purpose: Link a moved or renamed workspace URI to an existing workspace record.
When to use: call when the same repository appears under a new local path, mount
point, or machine-specific URI. Inputs: workspace_id_or_uri selects the existing
workspace; alias_uri is the new URI; reason and metadata document why it changed.
Side effects: writes an alias row. Output: alias record details. Failure modes:
fails if the referenced workspace cannot be resolved or the alias conflicts.
""".strip(),
        "params": {
            "workspace_id_or_uri": (
                "Existing workspace UUID, root URI, or alias URI to attach the alias to."
            ),
            "alias_uri": "New root URI or path alias that should resolve to the workspace.",
            "reason": "Short reason such as moved, renamed, cloned, or mounted.",
            "metadata": (
                "Optional JSON metadata explaining source machine, remote, or migration context."
            ),
        },
    },
    "list_workspace_aliases": {
        "title": "List workspace aliases",
        "description": """
Purpose: Inspect workspace alias mappings. When to use: call when an agent is
unsure whether two filesystem roots point to the same repository memory. Inputs:
workspace_id_or_uri optionally scopes the list. Side effects: none beyond
database reads. Output: alias rows including canonical workspace identifiers and
reasons. Failure modes: returns an empty list when no aliases exist or the filter
matches nothing.
""".strip(),
        "params": {
            "workspace_id_or_uri": (
                "Optional workspace UUID, root URI, or alias URI used to filter aliases."
            ),
        },
    },
    "record_workspace_fingerprints": {
        "title": "Record workspace fingerprints",
        "description": """
Purpose: Store durable repository identity fingerprints for alias detection.
When to use: call after discovering git remotes, first commits, or other stable
repo identifiers on a workspace. Inputs: workspace_id_or_uri selects the
workspace; fingerprints is a list of typed identity facts. Side effects: writes
fingerprint rows. Output: stored fingerprint records. Failure modes: fails when
the workspace cannot be resolved or fingerprint payloads are malformed.
""".strip(),
        "params": {
            "workspace_id_or_uri": "Workspace UUID, root URI, or alias URI receiving fingerprints.",
            "fingerprints": (
                "List of fingerprint objects such as git remote URLs or first commit IDs."
            ),
        },
    },
    "suggest_workspace_aliases": {
        "title": "Suggest workspace aliases",
        "description": """
Purpose: Suggest existing workspaces that may match a new alias URI based on
identity fingerprints. When to use: call before creating a new workspace for a
repo that may have moved. Inputs: alias_uri is the new path; fingerprints are
observed durable identifiers. Side effects: none beyond database reads. Output:
candidate workspace matches with confidence evidence. Failure modes: returns an
empty list when no fingerprints overlap.
""".strip(),
        "params": {
            "alias_uri": "New workspace URI or local path being evaluated.",
            "fingerprints": "Observed identity fingerprints for the candidate workspace.",
        },
    },
    "get_workspace_coordination_policy": {
        "title": "Get workspace coordination policy",
        "description": """
Purpose: Read reservation and conflict policy for a workspace. When to use: call
before reserving files or symbols in a multi-agent workflow. Inputs:
workspace_id_or_uri identifies the workspace. Side effects: none beyond database
reads. Output: current conflict policy and related coordination settings. Failure
modes: fails when the workspace cannot be resolved.
""".strip(),
        "params": {
            "workspace_id_or_uri": (
                "Workspace UUID, root URI, or alias URI whose policy should be read."
            ),
        },
    },
    "set_workspace_coordination_policy": {
        "title": "Set workspace coordination policy",
        "description": """
Purpose: Configure how the workspace handles reservation conflicts. When to use:
call during setup or team policy changes before multiple agents edit in parallel.
Inputs: workspace_id_or_uri identifies the workspace; reservation_conflict_policy
chooses advisory, strict, or override-with-reason. Side effects: updates workspace
policy. Output: updated policy record. Failure modes: invalid policy names are
rejected.
""".strip(),
        "params": {
            "workspace_id_or_uri": (
                "Workspace UUID, root URI, or alias URI whose policy should change."
            ),
            "reservation_conflict_policy": (
                "Conflict mode: advisory, strict, or override-with-reason."
            ),
        },
    },
    "record_changeset": {
        "title": "Record changeset",
        "description": """
Purpose: Persist a code changeset with files, optional patches, git metadata, and
session links. When to use: call after an agent edits or reviews files so future
agents can understand what changed and why. Inputs: files contains changed file
objects; workspace_id or workspace_uri is required; metadata links commits,
branches, intent, and sessions. Side effects: writes changeset and file rows.
Output: changeset identifiers and summary fields. Failure modes: raises when no
workspace identifier is supplied or payloads are invalid.
""".strip(),
        "params": {
            "files": "List of changed file objects with file_path, status, and optional patch.",
            "workspace_id": "Existing workspace UUID; required if workspace_uri is omitted.",
            "workspace_uri": "Workspace root URI used to create or resolve a workspace.",
            "workspace_name": "Optional display name when creating a workspace from workspace_uri.",
            "git_commit": "Optional git commit SHA associated with the changeset.",
            "branch": "Optional branch name associated with the changeset.",
            "intent": "Short explanation of why the changes were made.",
            "summary": "Human-readable summary of the changeset.",
            "metadata": "Optional JSON metadata for tools, test evidence, or external refs.",
            "session_id": "Optional internal session UUID to link to this changeset.",
            "session_external_id": (
                "Optional external transcript/session id to link to this changeset."
            ),
        },
    },
    "record_agent_action": {
        "title": "Record agent action",
        "description": """
Purpose: Log what an agent is doing in a workspace. When to use: call at the
start or end of meaningful work so other agents can see active intent and
progress. Inputs: workspace_id, agent_name, action_type, summary, optional
intent/status/session ids. Side effects: writes an activity row. Output: action_id
for future references. Failure modes: fails when the workspace id is invalid.
""".strip(),
        "params": {
            "workspace_id": "Workspace UUID where the action occurred.",
            "agent_name": "Name of the agent or tool performing the action.",
            "action_type": "Short action category such as edit, review, plan, test, or handoff.",
            "summary": "Concise human-readable activity summary.",
            "intent": "Optional reason or objective behind the action.",
            "status": "Action status such as recorded, in_progress, completed, or blocked.",
            "session_id": "Optional internal session UUID associated with the action.",
            "session_external_id": (
                "Optional external transcript/session id associated with the action."
            ),
        },
    },
    "reserve_files": {
        "title": "Reserve files",
        "description": """
Purpose: Reserve files so agents can coordinate parallel edits. When to use: call
before modifying files that another agent might also touch. Inputs: workspace_id,
agent_name, file_paths, purpose, TTL, and optional override reason. Side effects:
creates reservation rows and audit events. Output: reservation status and any
conflicts. Failure modes: strict policies may reject conflicts unless an override
reason is allowed.
""".strip(),
        "params": {
            "workspace_id": "Workspace UUID where files are being reserved.",
            "agent_name": "Name of the agent reserving the files.",
            "file_paths": "Repo-relative file paths to reserve.",
            "purpose": "Short reason for the reservation.",
            "ttl_minutes": "Reservation lifetime in minutes; null means use storage defaults.",
            "override_reason": "Reason for overriding a conflict when policy permits overrides.",
        },
    },
    "release_reservation": {
        "title": "Release file reservation",
        "description": """
Purpose: Release active file reservations after work is done or abandoned. When
to use: call at handoff, task completion, or when a stale reservation should be
cleared. Inputs: workspace_id plus reservation_id or file_path, optionally scoped
by agent_name. Side effects: updates reservation state and audit events. Output:
count of released reservations. Failure modes: returns zero when no matching
active reservation exists.
""".strip(),
        "params": {
            "workspace_id": "Workspace UUID containing the reservation.",
            "reservation_id": "Optional reservation UUID to release.",
            "file_path": "Optional repo-relative file path to release.",
            "agent_name": "Optional agent name used to scope the release.",
        },
    },
    "renew_reservation": {
        "title": "Renew file reservation",
        "description": """
Purpose: Extend active file reservations while work continues. When to use: call
before TTL expiry if an agent still owns the edit. Inputs: workspace_id plus
reservation_id or file_path, optional agent_name, and new TTL. Side effects:
updates reservation expiry and audit rows. Output: count of renewed reservations.
Failure modes: returns zero when no matching active reservation exists.
""".strip(),
        "params": {
            "workspace_id": "Workspace UUID containing the reservation.",
            "reservation_id": "Optional reservation UUID to renew.",
            "file_path": "Optional repo-relative file path to renew.",
            "agent_name": "Optional agent name used to scope renewal.",
            "ttl_minutes": "New reservation lifetime in minutes.",
        },
    },
    "get_active_reservations": {
        "title": "Get active file reservations",
        "description": """
Purpose: Read current file reservations for a workspace. When to use: call before
editing or reviewing files to detect coordination conflicts. Inputs: workspace_id
and optional file_paths filter. Side effects: none beyond database reads. Output:
active reservations with agents, purpose, TTL, and file paths. Failure modes:
returns an empty list when no active reservations match.
""".strip(),
        "params": {
            "workspace_id": "Workspace UUID whose file reservations should be listed.",
            "file_paths": "Optional repo-relative file paths used to filter reservations.",
        },
    },
    "reserve_symbols": {
        "title": "Reserve symbols",
        "description": """
Purpose: Reserve functions, classes, or other symbols for finer-grained parallel
coordination. When to use: call before editing shared APIs where file-level
reservation is too broad. Inputs: workspace_id, agent_name, symbols, purpose,
TTL, and optional override reason. Side effects: creates symbol reservation and
audit rows. Output: reservation status and conflicts. Failure modes: strict
policy may reject conflicting symbols.
""".strip(),
        "params": {
            "workspace_id": "Workspace UUID where symbols are being reserved.",
            "agent_name": "Name of the agent reserving the symbols.",
            "symbols": "Symbol names or qualified identifiers to reserve.",
            "purpose": "Short reason for the symbol reservation.",
            "ttl_minutes": "Reservation lifetime in minutes; null means use storage defaults.",
            "override_reason": "Reason for overriding a conflict when policy permits overrides.",
        },
    },
    "release_symbol_reservation": {
        "title": "Release symbol reservation",
        "description": """
Purpose: Release active symbol reservations. When to use: call after finishing
work on a function, class, API, or other reserved symbol. Inputs: workspace_id
plus reservation_id or symbol, optionally scoped by agent_name. Side effects:
updates reservation state and audit rows. Output: count of released symbol
reservations. Failure modes: returns zero when no matching reservation exists.
""".strip(),
        "params": {
            "workspace_id": "Workspace UUID containing the symbol reservation.",
            "reservation_id": "Optional symbol reservation UUID to release.",
            "symbol": "Optional symbol name or qualified identifier to release.",
            "agent_name": "Optional agent name used to scope the release.",
        },
    },
    "renew_symbol_reservation": {
        "title": "Renew symbol reservation",
        "description": """
Purpose: Extend active symbol reservations while an agent is still editing.
When to use: call before TTL expiry for ongoing API or function work. Inputs:
workspace_id plus reservation_id or symbol, optional agent_name, and TTL. Side
effects: updates reservation expiry and audit rows. Output: count of renewed
symbol reservations. Failure modes: returns zero when no matching active
reservation exists.
""".strip(),
        "params": {
            "workspace_id": "Workspace UUID containing the symbol reservation.",
            "reservation_id": "Optional symbol reservation UUID to renew.",
            "symbol": "Optional symbol name or qualified identifier to renew.",
            "agent_name": "Optional agent name used to scope renewal.",
            "ttl_minutes": "New reservation lifetime in minutes.",
        },
    },
    "get_symbol_conflicts": {
        "title": "Get symbol conflicts",
        "description": """
Purpose: Read active symbol reservations that could conflict with planned work.
When to use: call before changing shared APIs or named entities. Inputs:
workspace_id and optional symbols filter. Side effects: none beyond database
reads. Output: active symbol reservations with owners, purposes, and expiry.
Failure modes: returns an empty list when there are no active conflicts.
""".strip(),
        "params": {
            "workspace_id": "Workspace UUID whose symbol reservations should be checked.",
            "symbols": "Optional symbol names or qualified identifiers to filter conflicts.",
        },
    },
    "record_lsp_references": {
        "title": "Record LSP references",
        "description": """
Purpose: Import language-server reference edges into the code graph. When to use:
call after collecting definitions or references from an external LSP client.
Inputs: workspace_id, reference payloads, and replace flag. Side effects: writes
or replaces code graph reference rows. Output: import counts and status. Failure
modes: malformed reference payloads or invalid workspace ids are rejected.
""".strip(),
        "params": {
            "workspace_id": "Workspace UUID receiving LSP reference edges.",
            "references": "List of LSP-style reference objects with source and target locations.",
            "replace": "Whether to replace existing reference edges for the imported scope.",
        },
    },
    "review_workspace_context": {
        "title": "Review workspace context",
        "description": """
Purpose: Summarize relevant reservations, handoffs, lineage, and recent activity
before work starts. When to use: call at the beginning of a task to avoid
duplicating or conflicting with other agents. Inputs: workspace, intent, optional
file paths, symbols, agent name, and limit. Side effects: records an MCP audit
event. Output: compact review context with risks, reservations, handoffs, and
lineage. Failure modes: returns limited context when workspace history is sparse.
""".strip(),
        "params": {
            "workspace_id_or_uri": "Workspace UUID, root URI, or alias URI to review.",
            "intent": "Natural-language description of the planned work.",
            "file_paths": "Optional repo-relative files relevant to the planned work.",
            "symbols": "Optional symbol names relevant to the planned work.",
            "agent_name": "Optional requesting agent name for coordination context.",
            "limit": "Maximum number of context items per category.",
        },
    },
    "record_handoff_summary": {
        "title": "Record handoff summary",
        "description": """
Purpose: Store a structured handoff packet for the next agent or human reviewer.
When to use: call when pausing, finishing, or transferring a task. Inputs:
workspace_id, from/to agents, summary, next steps, blockers, tested commands,
remaining risks, next action, and template. Side effects: writes a handoff row.
Output: handoff_id for future retrieval. Failure modes: invalid workspace or
malformed list fields are rejected.
""".strip(),
        "params": {
            "workspace_id": "Workspace UUID where the handoff belongs.",
            "from_agent_name": "Agent that is leaving the handoff.",
            "summary": "Concise summary of completed work and current state.",
            "to_agent_name": "Optional intended receiving agent or role.",
            "next_steps": "Optional ordered next steps for the receiver.",
            "blocked_on": "Optional blockers that prevent progress.",
            "status": "Handoff status such as open, closed, or blocked.",
            "tested_commands": "Commands already run to validate the work.",
            "remaining_risks": "Known risks, caveats, or areas needing follow-up.",
            "next_action": "Single most important next action.",
            "template": "Handoff template name, usually standard.",
        },
    },
    "list_handoff_summaries": {
        "title": "List handoff summaries",
        "description": """
Purpose: Retrieve handoff packets for a workspace or across workspaces. When to
use: call when resuming a task, auditing pending work, or preparing context for
another agent. Inputs: optional workspace filter, status filter, and limit. Side
effects: none beyond database reads. Output: handoff summaries with status,
agents, next steps, blockers, and risks. Failure modes: returns an empty list
when no handoffs match.
""".strip(),
        "params": {
            "workspace_id_or_uri": (
                "Optional workspace UUID, root URI, or alias URI to filter handoffs."
            ),
            "status": "Optional handoff status filter such as open, closed, or blocked.",
            "limit": "Maximum number of handoffs to return.",
        },
    },
    "list_reservation_events": {
        "title": "List reservation events",
        "description": """
Purpose: Inspect audit history for reservation lifecycle events. When to use:
call during conflict analysis, stale reservation cleanup, or team coordination
review. Inputs: optional workspace, reservation kind, action filter, and limit.
Side effects: none beyond database reads. Output: created, renewed, released, and
expired reservation events. Failure modes: returns an empty list when no events
match.
""".strip(),
        "params": {
            "workspace_id_or_uri": (
                "Optional workspace UUID, root URI, or alias URI to filter events."
            ),
            "reservation_kind": "Optional kind filter such as file or symbol.",
            "action": (
                "Optional lifecycle action filter such as created, renewed, released, or expired."
            ),
            "limit": "Maximum number of audit events to return.",
        },
    },
    "close_handoff_summary": {
        "title": "Close handoff summary",
        "description": """
Purpose: Mark a handoff as consumed or no longer active. When to use: call after
the receiving agent has acted on the handoff or a human has reviewed it. Inputs:
handoff_id and final status. Side effects: updates handoff status. Output: count
of closed records. Failure modes: returns zero when the handoff id does not
match an active row.
""".strip(),
        "params": {
            "handoff_id": "Handoff UUID to close.",
            "status": "Final status to set, usually closed.",
        },
    },
    "get_workspace_lineage_graph": {
        "title": "Get workspace lineage graph",
        "description": """
Purpose: Return a graph of major collaboration artifacts for a workspace. When
to use: call when an agent needs a high-level map of sessions, changesets,
handoffs, reservations, and activity. Inputs: workspace_id and limit. Side
effects: none beyond database reads. Output: nodes and edges suitable for
dashboard or orchestration analysis. Failure modes: returns a sparse graph for
new or unindexed workspaces.
""".strip(),
        "params": {
            "workspace_id": "Workspace UUID whose lineage graph should be returned.",
            "limit": "Maximum number of lineage nodes or rows to include.",
        },
    },
    "get_agent_activity_events": {
        "title": "Get agent activity events",
        "description": """
Purpose: Return normalized activity events for dashboards and orchestrators.
When to use: call to inspect recent agent runs, reservations, handoffs,
changesets, or status transitions. Inputs: workspace_id plus optional limit,
kind, agent, and status filters. Side effects: none beyond database reads.
Output: event records normalized for UI or agent consumption. Failure modes:
returns an empty event list when no activity matches.
""".strip(),
        "params": {
            "workspace_id": "Workspace UUID whose activity should be read.",
            "limit": "Maximum number of events to return.",
            "kind": "Optional event kind filter.",
            "agent": "Optional agent name filter.",
            "status": "Optional event status filter.",
        },
    },
    "get_dashboard_overview": {
        "title": "Get dashboard overview",
        "description": """
Purpose: Return a compact read-only dashboard summary for one workspace. When to
use: call when a human reviewer, PM agent, or orchestrator needs current state
without reading raw transcripts. Inputs: workspace_id and limit. Side effects:
none beyond database reads. Output: summary cards, recent activity, handoffs,
reservations, and risk signals. Failure modes: returns sparse sections for a new
workspace.
""".strip(),
        "params": {
            "workspace_id": "Workspace UUID whose dashboard overview should be returned.",
            "limit": "Maximum number of recent rows to include in overview sections.",
        },
    },
}


def _format_tool_description(description: str) -> str:
    text = " ".join(description.strip().split())
    for heading in TOOL_DESCRIPTION_HEADINGS:
        text = text.replace(f" {heading}", f"\n{heading}")
    return text


def _apply_tool_metadata() -> None:
    for tool_name, metadata in TOOL_METADATA.items():
        tool = mcp._tool_manager.get_tool(tool_name)
        if tool is None:
            continue
        tool.title = metadata.get("title")
        description = _format_tool_description(str(metadata["description"]))
        tool.description = description
        tool.fn.__doc__ = description
        properties = tool.parameters.setdefault("properties", {})
        for param_name, param_description in metadata.get("params", {}).items():
            param_schema = properties.get(param_name)
            if isinstance(param_schema, dict):
                param_schema["description"] = param_description


@mcp.tool()
def get_geond_server_info() -> dict[str, Any]:
    """Return safe server metadata without opening the Geond database."""
    return {
        "name": "Geond Agent Protocol",
        "package": "geond-agent-protocol",
        "version": _installed_version(),
        "purpose": (
            "Local-first shared memory and coordination for AI coding agents "
            "working on the same repository."
        ),
        "safe_for_browser_try": True,
        "database_required": False,
        "recommended_first_call": True,
        "environment_variables": {
            "required": [],
            "optional": {
                "GEOND_DATABASE_URL": (
                    "PostgreSQL connection string for the default local or team database."
                ),
                "GEOND_DATABASE_PROFILE": (
                    "Profile selector for alternate database URLs, for example azure."
                ),
                "AZURE_GEOND_DATABASE_URL": (
                    "Shared Azure PostgreSQL connection string used when "
                    "GEOND_DATABASE_PROFILE=azure."
                ),
            },
        },
        "tool_groups": [
            {
                "name": "memory_search",
                "tools": [
                    "search_dev_memory",
                    "explain_change",
                    "get_changeset_detail",
                    "get_symbol_context",
                ],
                "use_when": (
                    "An agent needs prior context, evidence, or code history before editing."
                ),
            },
            {
                "name": "workspace_identity",
                "tools": [
                    "register_workspace_alias",
                    "list_workspace_aliases",
                    "record_workspace_fingerprints",
                    "suggest_workspace_aliases",
                ],
                "use_when": "A repository moves between local paths, machines, or shared profiles.",
            },
            {
                "name": "coordination",
                "tools": [
                    "review_workspace_context",
                    "reserve_files",
                    "release_reservation",
                    "renew_reservation",
                    "get_active_reservations",
                    "reserve_symbols",
                    "release_symbol_reservation",
                    "renew_symbol_reservation",
                    "get_symbol_conflicts",
                ],
                "use_when": "Multiple agents may edit the same files or symbols.",
            },
            {
                "name": "handoffs_and_audit",
                "tools": [
                    "record_agent_action",
                    "record_changeset",
                    "record_handoff_summary",
                    "list_handoff_summaries",
                    "close_handoff_summary",
                    "list_reservation_events",
                ],
                "use_when": (
                    "Work should be recorded for the next agent, reviewer, or PM dashboard."
                ),
            },
            {
                "name": "code_graph_and_dashboard",
                "tools": [
                    "record_lsp_references",
                    "get_workspace_lineage_graph",
                    "get_agent_activity_events",
                    "get_dashboard_overview",
                ],
                "use_when": (
                    "An agent or dashboard needs graph, activity, or overview read models."
                ),
            },
            {
                "name": "orchestration",
                "tools": [
                    "create_goal",
                    "create_run",
                    "create_task",
                    "register_worker_session",
                    "claim_task",
                    "renew_task_lease",
                    "release_task_lease",
                    "finish_task_with_handoff",
                    "record_command_evidence",
                    "get_readiness_report",
                ],
                "use_when": (
                    "A conductor or multiple worker agents need shared run/task/lease state."
                ),
            },
        ],
        "example_workflows": [
            [
                "review_workspace_context",
                "reserve_files",
                "record_changeset",
                "record_handoff_summary",
            ],
            ["search_dev_memory", "explain_change", "get_changeset_detail"],
            ["record_workspace_fingerprints", "suggest_workspace_aliases"],
        ],
        "setup_hints": [
            "Run `docker compose up -d postgres` for local PostgreSQL.",
            "Run `uv run geond migrate` before writing shared memory.",
            (
                "Run `uv run geond mcp-smoke --format text --allow-empty-search` "
                "to verify MCP transport."
            ),
            (
                "Use this tool first in Glama Try in Browser because it does not "
                "require database access."
            ),
        ],
        "related_servers": [
            "dl4rce/flaiwheel",
            "et-do/myelin",
            "bacharyehya/claude-memory-architecture",
        ],
    }


@mcp.tool()
def search_dev_memory(
    query: str,
    limit: int = 10,
    mode: str = "keyword",
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


@mcp.tool()
def create_goal(
    workspace_id_or_uri: str,
    title: str,
    summary: str = "",
    status: str = "accepted",
    created_by_agent: str | None = None,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Create an orchestration goal for a workspace."""
    with connect(get_settings()) as conn:
        return orchestration_store.create_goal(
            conn,
            workspace_id_or_uri=workspace_id_or_uri,
            title=title,
            summary=summary,
            status=status,
            created_by_agent=created_by_agent,
            metadata=metadata,
            idempotency_key=idempotency_key,
        )


@mcp.tool()
def create_run(
    workspace_id_or_uri: str,
    title: str,
    goal_id: str | None = None,
    risk_level: str = "medium",
    status: str = "active",
    created_by_agent: str | None = None,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Create an orchestration run under an optional goal."""
    with connect(get_settings()) as conn:
        return orchestration_store.create_run(
            conn,
            workspace_id_or_uri=workspace_id_or_uri,
            title=title,
            goal_id=goal_id,
            risk_level=risk_level,
            status=status,
            created_by_agent=created_by_agent,
            metadata=metadata,
            idempotency_key=idempotency_key,
        )


@mcp.tool()
def create_task(
    run_id: str,
    title: str,
    description: str = "",
    status: str = "ready",
    priority: int = 0,
    required_evidence: list[dict[str, Any]] | None = None,
    created_by_agent: str | None = None,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Create a claimable orchestration task in a run."""
    with connect(get_settings()) as conn:
        return orchestration_store.create_task(
            conn,
            run_id=run_id,
            title=title,
            description=description,
            status=status,
            priority=priority,
            required_evidence=required_evidence,
            created_by_agent=created_by_agent,
            metadata=metadata,
            idempotency_key=idempotency_key,
        )


@mcp.tool()
def update_task_state(
    task_id: str,
    status: str,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Update orchestration task status."""
    with connect(get_settings()) as conn:
        return orchestration_store.update_task_state(
            conn,
            task_id=task_id,
            status=status,
            metadata=metadata,
            idempotency_key=idempotency_key,
        )


@mcp.tool()
def register_worker_session(
    run_id: str,
    agent_name: str,
    status: str = "active",
    session_external_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Register a worker agent session for an orchestration run."""
    with connect(get_settings()) as conn:
        return orchestration_store.register_worker_session(
            conn,
            run_id=run_id,
            agent_name=agent_name,
            status=status,
            session_external_id=session_external_id,
            metadata=metadata,
            idempotency_key=idempotency_key,
        )


@mcp.tool()
def claim_task(
    task_id: str,
    agent_name: str,
    worker_session_id: str | None = None,
    ttl_minutes: int | None = 120,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Claim a ready task with a worker lease."""
    with connect(get_settings()) as conn:
        return orchestration_store.claim_task(
            conn,
            task_id=task_id,
            agent_name=agent_name,
            worker_session_id=worker_session_id,
            ttl_minutes=ttl_minutes,
            metadata=metadata,
            idempotency_key=idempotency_key,
        )


@mcp.tool()
def renew_task_lease(
    lease_id: str,
    worker_session_id: str | None = None,
    ttl_minutes: int | None = 120,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Renew an active orchestration task lease."""
    with connect(get_settings()) as conn:
        return orchestration_store.renew_task_lease(
            conn,
            lease_id=lease_id,
            worker_session_id=worker_session_id,
            ttl_minutes=ttl_minutes,
            idempotency_key=idempotency_key,
        )


@mcp.tool()
def release_task_lease(
    lease_id: str,
    reason: str = "released",
    worker_session_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Release an active orchestration task lease without completing the task."""
    with connect(get_settings()) as conn:
        return orchestration_store.release_task_lease(
            conn,
            lease_id=lease_id,
            reason=reason,
            worker_session_id=worker_session_id,
            idempotency_key=idempotency_key,
        )


@mcp.tool()
def finish_task_with_handoff(
    lease_id: str,
    summary: str,
    task_status: str = "done",
    tested_commands: list[str] | None = None,
    remaining_risks: list[str] | None = None,
    next_action: str | None = None,
    blocked_on: list[str] | None = None,
    worker_session_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Finish a leased task and record a structured handoff."""
    with connect(get_settings()) as conn:
        return orchestration_store.finish_task_with_handoff(
            conn,
            lease_id=lease_id,
            summary=summary,
            task_status=task_status,
            tested_commands=tested_commands,
            remaining_risks=remaining_risks,
            next_action=next_action,
            blocked_on=blocked_on,
            worker_session_id=worker_session_id,
            idempotency_key=idempotency_key,
        )


@mcp.tool()
def record_command_evidence(
    run_id: str,
    command: str,
    task_id: str | None = None,
    worker_session_id: str | None = None,
    purpose: str = "",
    status: str | None = None,
    exit_code: int | None = None,
    stdout_summary: str = "",
    stderr_summary: str = "",
    log_path: str | None = None,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Record command/test evidence for an orchestration run."""
    with connect(get_settings()) as conn:
        return orchestration_store.record_command_evidence(
            conn,
            run_id=run_id,
            command=command,
            task_id=task_id,
            worker_session_id=worker_session_id,
            purpose=purpose,
            status=status,
            exit_code=exit_code,
            stdout_summary=stdout_summary,
            stderr_summary=stderr_summary,
            log_path=log_path,
            metadata=metadata,
            idempotency_key=idempotency_key,
        )


@mcp.tool()
def record_review_finding(
    run_id: str,
    summary: str,
    severity: str = "P2",
    status: str = "open",
    reviewer: str | None = None,
    task_id: str | None = None,
    affected_refs: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Record a review finding for readiness gating."""
    with connect(get_settings()) as conn:
        return orchestration_store.record_review_finding(
            conn,
            run_id=run_id,
            summary=summary,
            severity=severity,
            status=status,
            reviewer=reviewer,
            task_id=task_id,
            affected_refs=affected_refs,
            metadata=metadata,
            idempotency_key=idempotency_key,
        )


@mcp.tool()
def resolve_review_finding(
    finding_id: str,
    status: str,
    reason: str = "",
    resolved_by: str | None = None,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Resolve a review finding so readiness can be recalculated."""
    with connect(get_settings()) as conn:
        return orchestration_store.resolve_review_finding(
            conn,
            finding_id=finding_id,
            status=status,
            reason=reason,
            resolved_by=resolved_by,
            metadata=metadata,
            idempotency_key=idempotency_key,
        )


@mcp.tool()
def record_decision(
    run_id: str,
    decision: str,
    task_id: str | None = None,
    status: str = "accepted",
    reason: str = "",
    decided_by: str | None = None,
    evidence_refs: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Record an orchestration decision with optional evidence refs."""
    with connect(get_settings()) as conn:
        return orchestration_store.record_decision(
            conn,
            run_id=run_id,
            decision=decision,
            task_id=task_id,
            status=status,
            reason=reason,
            decided_by=decided_by,
            evidence_refs=evidence_refs,
            metadata=metadata,
            idempotency_key=idempotency_key,
        )


@mcp.tool()
def request_approval(
    run_id: str,
    reason: str,
    task_id: str | None = None,
    risk_level: str = "high",
    requested_by_agent: str | None = None,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Request human approval for a high-risk orchestration action."""
    with connect(get_settings()) as conn:
        return orchestration_store.request_approval(
            conn,
            run_id=run_id,
            reason=reason,
            task_id=task_id,
            risk_level=risk_level,
            requested_by_agent=requested_by_agent,
            metadata=metadata,
            idempotency_key=idempotency_key,
        )


@mcp.tool()
def resolve_approval(
    approval_id: str,
    status: str,
    resolved_by: str | None = None,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Resolve an approval request as approved or denied."""
    with connect(get_settings()) as conn:
        return orchestration_store.resolve_approval(
            conn,
            approval_id=approval_id,
            status=status,
            resolved_by=resolved_by,
            metadata=metadata,
            idempotency_key=idempotency_key,
        )


@mcp.tool()
def get_run(run_id: str) -> dict[str, Any]:
    """Read a run with tasks, workers, leases, evidence, findings, and approvals."""
    with connect(get_settings()) as conn:
        return orchestration_store.get_run(conn, run_id)


@mcp.tool()
def list_runs(
    workspace_id_or_uri: str,
    status: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """List orchestration runs for one workspace."""
    with connect(get_settings()) as conn:
        return orchestration_store.list_runs(
            conn,
            workspace_id_or_uri=workspace_id_or_uri,
            status=status,
            limit=limit,
        )


@mcp.tool()
def get_claimable_tasks(
    run_id: str | None = None,
    workspace_id_or_uri: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """List ready tasks that do not have an active lease."""
    with connect(get_settings()) as conn:
        return orchestration_store.get_claimable_tasks(
            conn,
            run_id=run_id,
            workspace_id_or_uri=workspace_id_or_uri,
            limit=limit,
        )


@mcp.tool()
def get_readiness_report(run_id: str) -> dict[str, Any]:
    """Return an evidence-linked readiness report for one run."""
    with connect(get_settings()) as conn:
        return orchestration_store.get_readiness_report(conn, run_id)


@mcp.tool()
def get_orchestrator_brief(workspace_id_or_uri: str, limit: int = 25) -> dict[str, Any]:
    """Return active runs, claimable tasks, and blocker counts for one workspace."""
    with connect(get_settings()) as conn:
        return orchestration_store.get_orchestrator_brief(conn, workspace_id_or_uri, limit=limit)


@mcp.tool()
def get_run_handoff_package(run_id: str, limit: int = 100) -> dict[str, Any]:
    """Return a complete run handoff package for recovery or external orchestration."""
    with connect(get_settings()) as conn:
        return orchestration_store.get_run_handoff_package(conn, run_id, limit=limit)


@mcp.tool()
def summarize_run(run_id: str) -> dict[str, Any]:
    """Return a deterministic Markdown and JSON summary for one run."""
    with connect(get_settings()) as conn:
        return orchestration_store.summarize_run(conn, run_id)


@mcp.tool()
def get_orchestrator_plan(
    workspace_id_or_uri: str,
    run_id: str | None = None,
    agents: list[str] | None = None,
    limit: int = 50,
    base_dir: str = "tmp/geond-runs",
) -> dict[str, Any]:
    """Return a read-only Geond Orchestrator plan for a workspace or run."""
    with connect(get_settings()) as conn:
        return orchestrator_planner.create_plan(
            conn,
            workspace_id_or_uri=workspace_id_or_uri,
            run_id=run_id,
            agents=agents,
            limit=limit,
            base_dir=Path(base_dir),
            write_bundle=False,
        )


@mcp.tool()
def preview_orchestrator_agent_step(
    run_id: str,
    agents: list[str] | None = None,
    max_workers: int = 1,
    model: str | None = None,
    sandbox: str = "workspace-write",
    timeout_seconds: int = 3600,
    limit: int = 50,
    base_dir: str = "tmp/geond-runs",
) -> dict[str, Any]:
    """Preview the next Agent Mode action without executing or writing state."""
    with connect(get_settings()) as conn:
        return orchestrator_mcp_bridge.preview_agent_step(
            conn,
            run_id,
            agents=agents,
            max_workers=max_workers,
            model=model,
            sandbox=sandbox,
            timeout_seconds=timeout_seconds,
            base_dir=Path(base_dir),
            limit=limit,
        )


@mcp.tool()
def propose_orchestrator_task_graph(
    run_id: str,
    planner: str = "template",
    template: str = "auto",
    planner_agent: str = "codex",
    base_dir: str = "tmp/geond-runs",
) -> dict[str, Any]:
    """Return a read-only task graph proposal or LLM planner preview for one run."""
    with connect(get_settings()) as conn:
        return orchestrator_task_planner.propose_task_graph(
            conn,
            run_id,
            planner=planner,
            template=template,
            agent_name=planner_agent,
            execute_planner=False,
            base_dir=Path(base_dir),
        )


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


@mcp.resource("geond://workspaces/{workspace_id}/orchestrator-brief", mime_type="application/json")
def workspace_orchestrator_brief_resource(workspace_id: str) -> dict[str, Any]:
    """Read a compact orchestration brief for a workspace."""
    with connect(get_settings()) as conn:
        return orchestration_store.get_orchestrator_brief(conn, workspace_id)


@mcp.resource("geond://runs/{run_id}", mime_type="application/json")
def run_resource(run_id: str) -> dict[str, Any]:
    """Read a complete orchestration run state."""
    with connect(get_settings()) as conn:
        return orchestration_store.get_run(conn, run_id)


@mcp.resource("geond://runs/{run_id}/tasks", mime_type="application/json")
def run_tasks_resource(run_id: str) -> dict[str, Any]:
    """Read tasks for an orchestration run."""
    with connect(get_settings()) as conn:
        run = orchestration_store.get_run(conn, run_id)
        return {
            "schema": "geond.task.v1.list",
            "status": run.get("status"),
            "code": run.get("code"),
            "run_id": run_id,
            "tasks": run.get("tasks", []),
        }


@mcp.resource("geond://runs/{run_id}/workers", mime_type="application/json")
def run_workers_resource(run_id: str) -> dict[str, Any]:
    """Read worker sessions for an orchestration run."""
    with connect(get_settings()) as conn:
        run = orchestration_store.get_run(conn, run_id)
        return {
            "schema": "geond.worker_session.v1.list",
            "status": run.get("status"),
            "code": run.get("code"),
            "run_id": run_id,
            "workers": run.get("workers", []),
        }


@mcp.resource("geond://runs/{run_id}/leases", mime_type="application/json")
def run_leases_resource(run_id: str) -> dict[str, Any]:
    """Read task leases for an orchestration run."""
    with connect(get_settings()) as conn:
        run = orchestration_store.get_run(conn, run_id)
        return {
            "schema": "geond.task_lease.v1.list",
            "status": run.get("status"),
            "code": run.get("code"),
            "run_id": run_id,
            "leases": run.get("leases", []),
        }


@mcp.resource("geond://runs/{run_id}/readiness", mime_type="application/json")
def run_readiness_resource(run_id: str) -> dict[str, Any]:
    """Read readiness status for an orchestration run."""
    with connect(get_settings()) as conn:
        return orchestration_store.get_readiness_report(conn, run_id)


_ORCH_PARAM_DESCRIPTIONS = {
    "workspace_id_or_uri": "Workspace UUID, root URI, or alias URI for orchestration state.",
    "title": "Human-readable goal, run, or task title.",
    "summary": "Concise human-readable summary.",
    "status": "Lifecycle status for the object being created or updated.",
    "created_by_agent": "Optional agent name that created the object.",
    "metadata": "Optional JSON metadata for client-specific context.",
    "idempotency_key": "Optional stable key that makes write retries safe.",
    "goal_id": "Optional orchestration goal UUID that owns the run.",
    "risk_level": "Run or approval risk level: low, medium, high, or critical.",
    "run_id": "Orchestration run UUID.",
    "task_id": "Orchestration task UUID.",
    "description": "Longer task description or implementation note.",
    "priority": "Task ordering priority; larger values are listed first.",
    "required_evidence": "Evidence requirements the worker should satisfy.",
    "agent_name": "Worker agent name such as codex or claude.",
    "session_external_id": "Optional external transcript or session id.",
    "worker_session_id": "Worker session UUID that owns or updates the lease.",
    "ttl_minutes": "Lease lifetime in minutes; null means no expiry.",
    "lease_id": "Task lease UUID.",
    "reason": "Reason for a decision, approval, release, or status change.",
    "task_status": "Final task status, usually done or blocked.",
    "tested_commands": "Commands already run to validate the task.",
    "remaining_risks": "Known risks that remain after worker handoff.",
    "next_action": "Single next action for the receiver or orchestrator.",
    "blocked_on": "Blockers that prevented completion.",
    "command": "Command, query, or validation action that produced evidence.",
    "purpose": "Why the command or evidence was recorded.",
    "exit_code": "Process exit code when command evidence came from a command.",
    "stdout_summary": "Short redacted stdout summary.",
    "stderr_summary": "Short redacted stderr summary.",
    "log_path": "Path to a local full log file, if retained.",
    "severity": "Review finding severity such as P0, P1, P2, or P3.",
    "finding_id": "Review finding UUID.",
    "reviewer": "Human or model reviewer that raised the finding.",
    "affected_refs": "Files, resources, or evidence refs affected by the finding.",
    "decision": "Decision statement to record in the run ledger.",
    "decided_by": "Human or agent that made the decision.",
    "evidence_refs": "Evidence references supporting the decision.",
    "requested_by_agent": "Agent requesting approval.",
    "approval_id": "Approval request UUID.",
    "resolved_by": "Human or agent that resolved the approval.",
    "limit": "Maximum number of records to return.",
    "agents": "Ordered worker agent pool such as ['codex', 'claude'] for planning or preview.",
    "max_workers": "Maximum number of spawned workers Agent Mode may select for one dispatch step.",
    "model": "Optional model name passed through to the spawned worker adapter.",
    "sandbox": "Worker sandbox profile, usually workspace-write.",
    "timeout_seconds": "Maximum spawned worker runtime in seconds.",
    "base_dir": "Local-only run artifact base directory, usually tmp/geond-runs.",
    "planner": "Task graph planner implementation, either template or llm.",
    "planner_agent": "LLM planner agent name for preview-only planning, such as codex or claude.",
    "template": "Task graph proposal template: auto, bugfix, implementation, docs, or ops.",
}


def _orchestration_metadata(
    *,
    title: str,
    purpose: str,
    params: list[str],
    output: str,
) -> dict[str, Any]:
    side_effects = (
        "writes orchestration state"
        if not title.startswith(("Get", "List", "Preview"))
        else "none beyond database reads"
    )
    return {
        "title": title,
        "description": (
            f"Purpose: {purpose} When to use: use this in MCP-first orchestration "
            "flows before building a higher-level Geond Orchestrator. Inputs: "
            "parameters identify the workspace, run, task, worker, or evidence record. "
            f"Side effects: {side_effects}. "
            f"Output: {output}. Failure modes: returns a stable status/code payload such as "
            "RUN_NOT_FOUND, TASK_NOT_CLAIMABLE, LEASE_CONFLICT, or IDEMPOTENCY_CONFLICT."
        ),
        "params": {name: _ORCH_PARAM_DESCRIPTIONS[name] for name in params},
    }


TOOL_METADATA.update(
    {
        "create_goal": _orchestration_metadata(
            title="Create orchestration goal",
            purpose="Create a top-level goal for a workspace.",
            params=[
                "workspace_id_or_uri",
                "title",
                "summary",
                "status",
                "created_by_agent",
                "metadata",
                "idempotency_key",
            ],
            output="a geond.goal.v1 payload",
        ),
        "create_run": _orchestration_metadata(
            title="Create orchestration run",
            purpose="Create a run under a goal or directly in a workspace.",
            params=[
                "workspace_id_or_uri",
                "title",
                "goal_id",
                "risk_level",
                "status",
                "created_by_agent",
                "metadata",
                "idempotency_key",
            ],
            output="a geond.run.v1 payload",
        ),
        "create_task": _orchestration_metadata(
            title="Create orchestration task",
            purpose="Create a ready task that a worker can claim.",
            params=[
                "run_id",
                "title",
                "description",
                "status",
                "priority",
                "required_evidence",
                "created_by_agent",
                "metadata",
                "idempotency_key",
            ],
            output="a geond.task.v1 payload",
        ),
        "update_task_state": _orchestration_metadata(
            title="Update orchestration task",
            purpose="Update the lifecycle status of a task.",
            params=["task_id", "status", "metadata", "idempotency_key"],
            output="an updated geond.task.v1 payload",
        ),
        "register_worker_session": _orchestration_metadata(
            title="Register worker session",
            purpose="Register a Codex, Claude, or other worker for a run.",
            params=[
                "run_id",
                "agent_name",
                "status",
                "session_external_id",
                "metadata",
                "idempotency_key",
            ],
            output="a geond.worker_session.v1 payload",
        ),
        "claim_task": _orchestration_metadata(
            title="Claim orchestration task",
            purpose="Claim a ready task and create a task lease.",
            params=[
                "task_id",
                "agent_name",
                "worker_session_id",
                "ttl_minutes",
                "metadata",
                "idempotency_key",
            ],
            output="a geond.task_lease.v1 payload with task and worker details",
        ),
        "renew_task_lease": _orchestration_metadata(
            title="Renew task lease",
            purpose="Renew an active task lease heartbeat.",
            params=["lease_id", "worker_session_id", "ttl_minutes", "idempotency_key"],
            output="an updated geond.task_lease.v1 payload",
        ),
        "release_task_lease": _orchestration_metadata(
            title="Release task lease",
            purpose="Release a task lease without marking the task done.",
            params=["lease_id", "reason", "worker_session_id", "idempotency_key"],
            output="an updated geond.task_lease.v1 payload",
        ),
        "finish_task_with_handoff": _orchestration_metadata(
            title="Finish task with handoff",
            purpose="Finish a leased task and record worker handoff data.",
            params=[
                "lease_id",
                "summary",
                "task_status",
                "tested_commands",
                "remaining_risks",
                "next_action",
                "blocked_on",
                "worker_session_id",
                "idempotency_key",
            ],
            output="a completed geond.task.v1 payload plus handoff id",
        ),
        "record_command_evidence": _orchestration_metadata(
            title="Record command evidence",
            purpose="Record validation command evidence for a run.",
            params=[
                "run_id",
                "command",
                "task_id",
                "worker_session_id",
                "purpose",
                "status",
                "exit_code",
                "stdout_summary",
                "stderr_summary",
                "log_path",
                "metadata",
                "idempotency_key",
            ],
            output="a geond.command_run.v1 payload",
        ),
        "record_review_finding": _orchestration_metadata(
            title="Record review finding",
            purpose="Record a readiness-gating review finding.",
            params=[
                "run_id",
                "summary",
                "severity",
                "status",
                "reviewer",
                "task_id",
                "affected_refs",
                "metadata",
                "idempotency_key",
            ],
            output="a geond.review_finding.v1 payload",
        ),
        "resolve_review_finding": _orchestration_metadata(
            title="Resolve review finding",
            purpose="Resolve or waive a readiness-gating review finding.",
            params=[
                "finding_id",
                "status",
                "reason",
                "resolved_by",
                "metadata",
                "idempotency_key",
            ],
            output="an updated geond.review_finding.v1 payload",
        ),
        "record_decision": _orchestration_metadata(
            title="Record orchestration decision",
            purpose="Record a decision and optional evidence refs.",
            params=[
                "run_id",
                "decision",
                "task_id",
                "status",
                "reason",
                "decided_by",
                "evidence_refs",
                "metadata",
                "idempotency_key",
            ],
            output="a geond.decision.v1 payload",
        ),
        "request_approval": _orchestration_metadata(
            title="Request approval",
            purpose="Request approval for a high-risk action.",
            params=[
                "run_id",
                "reason",
                "task_id",
                "risk_level",
                "requested_by_agent",
                "metadata",
                "idempotency_key",
            ],
            output="a geond.approval_request.v1 payload",
        ),
        "resolve_approval": _orchestration_metadata(
            title="Resolve approval",
            purpose="Approve or deny a pending approval request.",
            params=["approval_id", "status", "resolved_by", "metadata", "idempotency_key"],
            output="an updated geond.approval_request.v1 payload",
        ),
        "get_run": _orchestration_metadata(
            title="Get orchestration run",
            purpose="Read a complete run state.",
            params=["run_id"],
            output="run, task, worker, lease, evidence, finding, and approval sections",
        ),
        "list_runs": _orchestration_metadata(
            title="List orchestration runs",
            purpose="List runs for a workspace.",
            params=["workspace_id_or_uri", "status", "limit"],
            output="a compact geond.run.v1 list payload",
        ),
        "get_claimable_tasks": _orchestration_metadata(
            title="Get claimable tasks",
            purpose="List ready tasks without active leases.",
            params=["run_id", "workspace_id_or_uri", "limit"],
            output="a geond.task.v1 claimable list payload",
        ),
        "get_readiness_report": _orchestration_metadata(
            title="Get readiness report",
            purpose="Evaluate whether a run is ready, blocked, or awaiting approval.",
            params=["run_id"],
            output="a geond.readiness_report.v1 payload",
        ),
        "get_orchestrator_brief": _orchestration_metadata(
            title="Get orchestrator brief",
            purpose="Read active runs, claimable tasks, and blocker counts.",
            params=["workspace_id_or_uri", "limit"],
            output="a geond.orchestrator_brief.v1 payload",
        ),
        "get_run_handoff_package": _orchestration_metadata(
            title="Get run handoff package",
            purpose="Read all state needed to recover or hand off a run.",
            params=["run_id", "limit"],
            output="a geond.run_handoff_package.v1 payload",
        ),
        "summarize_run": _orchestration_metadata(
            title="Get run summary",
            purpose="Build a deterministic run summary without LLM calls.",
            params=["run_id"],
            output="a geond.run_summary.v1 payload with Markdown and JSON summary",
        ),
        "get_orchestrator_plan": _orchestration_metadata(
            title="Get orchestrator plan",
            purpose="Build a read-only Plan Mode payload for a workspace or run.",
            params=["workspace_id_or_uri", "run_id", "agents", "limit", "base_dir"],
            output="a geond.orchestrator_plan.v1 payload",
        ),
        "preview_orchestrator_agent_step": _orchestration_metadata(
            title="Preview orchestrator agent step",
            purpose="Select the next Agent Mode action without executing or writing state.",
            params=[
                "run_id",
                "agents",
                "max_workers",
                "model",
                "sandbox",
                "timeout_seconds",
                "limit",
                "base_dir",
            ],
            output="a geond.orchestrator_control.v1 preview payload",
        ),
        "propose_orchestrator_task_graph": _orchestration_metadata(
            title="Get task graph proposal",
            purpose="Build a template task graph proposal or read-only LLM planner preview.",
            params=["run_id", "planner", "template", "planner_agent", "base_dir"],
            output=(
                "a geond.task_graph_proposal.v1 proposal or geond.llm_task_graph_planner.v1 preview"
            ),
        ),
    }
)


_apply_tool_metadata()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
