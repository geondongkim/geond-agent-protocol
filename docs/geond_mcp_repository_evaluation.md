# Geond MCP Repository Evaluation

## Purpose

This document evaluates `geond-agent-protocol` against an open-source MCP repository rubric focused on independent core engines, MCP or Codex Skill wrappers, enterprise risk, cost control, and extensible agent workflows.

Use it when deciding whether Geond is a good substrate for cross-agent development memory, Codex/Claude/Copilot interoperability, PM-facing operational visibility, enterprise-oriented MCP adoption, or future AI usage observability.

## Scope

Repository root:

```text
C:\Users\EL035\dataschool\RealMe_OPIc
```

GitHub remote:

```text
https://github.com/geondongkim/geond-agent-protocol.git
```

Compared scenarios:

- **Current repository**: existing code and docs, without a consistently enforced operating loop.
- **Recommended operating loop**: agents routinely import sessions, read overview/handoffs, record actions, reserve work, and leave handoffs.
- **Usage observability extension**: operating loop plus token, cost, and usage-versus-evidence telemetry.

## Executive Summary

Geond is stronger than a typical one-purpose MCP server because it is not just a tool wrapper. It is a local-first development memory and coordination substrate with a Python CLI, stdio MCP server, session importers, Postgres/pgvector storage, redaction, search, code graph indexing, reservations, structured handoffs, changesets, dashboard read models, and a local dashboard UI.

The repository fits the "independent core engine plus MCP or Skill wrapper" strategy well. The weakest areas are enterprise IAM, complete MCP call auditing, and lightweight deployment compared with single-binary MCP tools.

## Scorecard

| Scenario | Score | Judgment |
| --- | ---: | --- |
| Current repository only | 28 / 40 | Strong as an agent-memory MCP repository. Needs IAM, audit, and usage accounting before enterprise-grade deployment. |
| Recommended operating loop applied | 33 / 40 | Strong candidate for practical multi-agent work. Value increases when agents consistently import sessions, record actions, reserve work, and leave handoffs. |
| Operating loop plus token and cost telemetry | 36 / 40 estimated | Strong PM and platform visibility story. Still needs IAM and external audit sinks for enterprise readiness. |

## Detailed Criteria

| Criterion | Current | With operating loop | Evidence and judgment |
| --- | ---: | ---: | --- |
| 1. Core engine and interface separation | 4.0 | 4.0 | The `geond` CLI and `geond-mcp` entry points are separate. The MCP server wraps storage, retrieval, dashboard, and coordination functions. It is not a pure external-binary wrapper, but the core package is cleanly callable from CLI, MCP, tests, and future Skills. |
| 2. Cross-platform interface | 4.0 | 4.5 | Geond supports stdio MCP and docs for VS Code MCP, Claude Desktop, Continue, Codex session import, Claude Code import, and VS Code Copilot import. HTTP or SSE MCP transport is not yet the primary design. |
| 3. CLI execution and lightweight deployment | 3.5 | 4.0 | The CLI is package-native and easy to run with `uv`, but Postgres and pgvector make it heavier than single-binary MCP tools. The tradeoff buys durable memory, indexed search, shared DB use, and collaboration primitives. |
| 4. Declarative schema and Codex portability | 4.0 | 4.5 | FastMCP typed functions map naturally to tool schemas. Tools such as `search_dev_memory`, `record_agent_action`, `reserve_files`, `reserve_symbols`, and `record_handoff_summary` are good Codex Skill candidates. |
| 5. Context control | 4.0 | 4.5 | Search, dashboard, resource, and context review paths use limits, candidate limits, message limits, snippets, and embedding text truncation. The operating loop should prefer handoffs and summaries before raw session expansion. |
| 6. Data privacy and redaction | 3.5 | 4.0 | Import paths redact API keys, bearer tokens, GitHub tokens, URL passwords, secret-like keys, invalid Unicode, and NUL values. Workspace/table/command sandboxing is still limited. |
| 7. IAM and access control | 1.5 | 2.0 | Geond is currently local-first and DB-credential based. It does not yet provide per-user JWT, API key impersonation, role-based MCP access, or enterprise identity delegation. |
| 8. Audit trail and monitoring | 3.5 | 4.0 | The DB stores sessions, events, messages, actions, reservations, reservation events, handoffs, changesets, benchmarks, and redaction findings. It does not yet record every MCP tool input and output in a dedicated audit stream. |

## Lightweight MCP Comparison

This comparison assumes a minimal single-binary or small Node/Rust/Go MCP server with no external database.

| Dimension | Single-binary lightweight MCP | Geond |
| --- | --- | --- |
| Cold start | Usually faster. One process starts and lists tools quickly. | Slower. Python runtime, settings, DB connectivity, and imports add startup cost. |
| Installation | Often easier. One binary or package command. | Requires Python/uv plus Postgres/pgvector for full functionality. |
| Simple tool call latency | Usually lower. | Higher if each call needs DB access. |
| Large session search | Often weaker unless it builds its own index. | Stronger. Uses Postgres full-text search, trigram, pgvector, and normalized tables. |
| Durable memory | Usually ad hoc or file-based. | Strong. Sessions, messages, events, changesets, handoffs, and reservations are normalized. |
| Multi-agent coordination | Usually not built in. | Strong. Reservations, handoffs, lineage, activity events, and dashboard are central concepts. |
| PM dashboard | Usually absent. | Built in as a read-only local dashboard. |
| Shared team memory | Requires extra infrastructure. | Natural with shared Postgres or Azure PostgreSQL. |
| Enterprise governance | Depends on custom work. | Better data model foundation, but IAM and audit streaming still need work. |

## Performance Judgment

Geond is not optimized for the fastest possible first MCP response. It is optimized for persistent, searchable, inspectable development memory.

Use a lightweight single-binary MCP when the tool is stateless, wraps one API or one CLI, startup latency matters more than memory quality, and there is no need for shared workspace history.

Use Geond when agents need prior session evidence, multiple agents may work in one repository, handoffs and reservations matter, PMs need dashboard visibility, or a team wants queryable memory across Codex, Claude Code, Copilot, and MCP clients.

## Enterprise Gaps

The main gaps are:

- no first-class user or team IAM model
- no JWT or API key impersonation path
- no DB row-level security policy documented for multi-tenant teams
- no dedicated `mcp_audit_events` table
- no Datadog, ELK, CloudWatch, or OpenTelemetry sink
- no complete token and cost accounting layer
- no policy separating personal productivity surveillance from team enablement metrics

## Decision

Geond should be treated as a strong alpha platform for agent memory and collaboration, not as a tiny MCP wrapper. Its design is well aligned with the independent core engine strategy because the core package can be surfaced through CLI, MCP, local dashboard, and future Codex Skills.

The best next step is not a broad rewrite. The best next step is to make the intended operating loop executable and observable:

1. Add `start-task` and `finish-task` wrappers.
2. Add `llm_usage_events`.
3. Add usage versus evidence dashboard views.
4. Add audit logging for MCP calls.
5. Decide the IAM path only after the local-first workflow is consistently valuable.

