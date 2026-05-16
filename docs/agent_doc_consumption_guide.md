# Agent Document Consumption Guide

## Purpose

This guide helps future agents understand which Geond documents to read and how to use them without loading the whole repository into context.

Agents should use this file as a routing table.

## Fast Routing

| Task type | Read first | Then read |
| --- | --- | --- |
| Evaluate Geond as an MCP repository | `docs/geond_mcp_repository_evaluation.md` | `docs/architecture.md`, `docs/mcp_client_config.md` |
| Start or continue multi-agent work | `docs/agent_operating_loop.md` | `docs/agent_collaboration.md`, `docs/agent_activity_dashboard.md` |
| Build AI usage or token metrics | `docs/ai_usage_observability.md` | `docs/geond_roadmap_backlog.md`, `schemas/001_initial.sql` |
| Pick next implementation work | `docs/geond_roadmap_backlog.md` | `docs/improvement_backlog.md`, `docs/implementation_plan.md` |
| Configure MCP clients | `docs/mcp_client_config.md` | `examples/mcp_clients/` |
| Understand dashboard behavior | `docs/agent_activity_dashboard.md` | `src/geond/dashboard_server.py`, `src/geond/storage/dashboard.py` |
| Understand session importers | `docs/agent_testbeds.md` | `src/geond/adapters/`, `tests/fixtures/` |
| Understand workspace identity | `docs/workspace_identity_and_search.md` | `src/geond/workspace_identity.py` |

## Reading Order For New Agents

If the task is vague and mentions Geond, agent memory, other chats, other agents, PM dashboards, token usage, or MCP:

1. Read this file.
2. Read `docs/agent_operating_loop.md`.
3. Read the routing row that matches the task.
4. Inspect the relevant code files.
5. Run `git status --short --branch`.
6. Make the smallest useful change.

## Document Design Rules

New Geond docs should use the following structure so agents can parse them quickly.

```text
# Title

## Purpose
One paragraph saying why this file exists.

## When To Read This
Bullets that map tasks to this document.

## Current State
What exists today.

## Proposed State
What should exist.

## Commands Or APIs
Concrete commands, tool names, schemas, or endpoints.

## Acceptance Criteria
Machine-checkable or reviewer-checkable criteria.

## Risks
Known tradeoffs and failure modes.

## Agent Guidance
Short instructions for future agents.
```

## Agent-Friendly Writing Rules

- Put the most important routing information near the top.
- Prefer tables for status, priority, and ownership.
- Use stable IDs for backlog items, such as `USAGE-001`.
- Include exact command names.
- Include file paths when a statement depends on implementation.
- Separate current behavior from proposed behavior.
- Label estimates clearly.
- Avoid writing docs that only describe aspirations without acceptance criteria.
- Avoid raw secrets, tokens, full local credentials, and private transcripts.
- Prefer "review signal" over "performance score" for usage metrics.

## Machine-Readable Conventions

| Marker | Meaning |
| --- | --- |
| `Priority: P0` | Must happen before broader work. |
| `Priority: P1` | Next high-value implementation work. |
| `Acceptance criteria` | Required verification target. |
| `Proposed command` | CLI behavior that may not exist yet. |
| `Current status` | Behavior that should exist now. |
| `Estimated` | Not exact; verify before using in reports. |

## How Agents Should Use Geond Docs

For implementation tasks:

1. Read the relevant doc.
2. Convert the selected backlog item into one small vertical slice.
3. Search code for the named commands, tables, or functions.
4. Implement storage first, then CLI/MCP, then dashboard.
5. Add tests near existing test families.
6. Update the doc only for behavior that now exists or for a newly clarified decision.

For review tasks:

1. Check whether the implementation matches the doc acceptance criteria.
2. Prioritize bugs, missing verification, privacy leaks, and metric misuse.
3. Call out when a proposed feature could create tokenmaxxing incentives.

For PM summaries:

1. Use workspace/team rollups first.
2. Mention data quality and estimate ratios.
3. Do not rank individuals by raw token count unless explicitly requested and governed by policy.
4. Prefer usage versus evidence metrics.

## Context Packs

### MCP Evaluation Pack

- `docs/geond_mcp_repository_evaluation.md`
- `docs/mcp_client_config.md`
- `src/geond/mcp_server/server.py`
- `pyproject.toml`

### Multi-Agent Operating Pack

- `docs/agent_operating_loop.md`
- `docs/agent_collaboration.md`
- `docs/agent_activity_dashboard.md`
- `src/geond/storage/repository.py`
- `src/geond/storage/dashboard.py`

### AI Usage Pack

- `docs/ai_usage_observability.md`
- `docs/geond_roadmap_backlog.md`
- `schemas/001_initial.sql`
- `src/geond/adapters/codex.py`
- `src/geond/adapters/claude_code.py`
- `src/geond/adapters/vscode_copilot.py`

## Final Reminder

Geond's differentiator is not raw token counting. It is evidence-linked development memory. Agents should preserve that framing when extending the project.

