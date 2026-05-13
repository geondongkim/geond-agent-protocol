# Reference Repo Analysis And Benchmark Plan

This document records the local reference repositories cloned under `repo/`.
The directory is intentionally local-only and ignored by git.

## Snapshot

Captured on 2026-05-13.

| Reference | Local path | HEAD | Primary surface | Notes |
| --- | --- | --- | --- | --- |
| roboticforce/remembrallmcp | `repo/remembrallmcp` | `5c5413b` | Persistent memory, tree-sitter code graph, pgvector, MCP | Strongest reference for code graph and memory retrieval benchmarks. |
| dazeb/mcp-handoff-server | `repo/mcp-handoff-server` | `5e5e43e` | Structured handoff documents | Useful baseline for handoff lifecycle ergonomics. |
| rinadelph/Agent-MCP | `repo/Agent-MCP` | `13d98b2` | Multi-agent orchestration, task management, dashboard, file-level locking | Useful baseline for agent coordination UX and task lifecycle. |
| justanotherspy/mcp-agent-memory | `repo/mcp-agent-memory` | `b94c06b` | Shared memory, CRUD/search/statistics, file locking | Useful baseline for lightweight local collaboration and concurrency safety. |
| agency-agents/mcp-memory | not cloned | not found | Claimed memory/handoff reference | `git ls-remote` and `gh search repos` did not find a public repository at that slug. Keep as unverified until a concrete URL is available. |

## What Each Reference Proves

### RemembrallMCP

Observed capabilities:

- Rust core with Postgres and pgvector.
- MCP tools for memory store/recall/update/delete, GitHub/doc ingestion, project indexing, impact analysis, and symbol lookup.
- Tree-sitter code graph across eight languages.
- Published benchmark posture around tool-call reduction, graph correctness, long-memory behavior, and agent productivity.

GEOND lesson:

- GEOND should not try to win by claiming "AST plus memory" alone. That surface is already represented.
- GEOND's defensible product surface is the coupling of graph-derived symbol scope, reservations, patch evidence, and structured handoffs.
- GEOND needs a graph correctness suite that compares expected symbols and edges, not only retrieval latency.

### MCP Handoff Server

Observed capabilities:

- Simple document lifecycle: create, read, update, complete, archive, list.
- File-system backed `handoff-system/active`, `archive`, and `templates` folders.
- MCP and HTTP modes.

GEOND lesson:

- GEOND handoffs should stay structured and queryable rather than becoming plain markdown notes.
- The handoff UX should still be as easy as this reference: one command to create, one command to consume, one command to close.
- GEOND should add "handoff packages" that bind summary, intent, patch, evidence refs, reservation ids, and verification results.

### Agent-MCP

Observed capabilities:

- Multi-agent orchestration with agent creation, task assignment, task status, project RAG, messaging, and dashboard concepts.
- File-level locking described as a coordination mechanism.
- Strong product story around short-lived focused agents, audit trails, and limited context.

GEOND lesson:

- GEOND should remain a protocol and evidence layer, not a full agent orchestrator.
- However, GEOND should expose enough task/reservation state for external orchestrators to build dashboards and agent assignment flows.
- File-level locking is a baseline. GEOND should use symbol and dependency-aware reservations as the higher-value coordination primitive.

### MCP Agent Memory

Observed capabilities:

- FastMCP Python server with add/read/update/delete/get/search/stats/clear/health_check tools.
- JSON file storage with cross-platform file locking, atomic writes, backups, and corruption recovery.
- Advanced filtering by agent, tags, priority, dates, and search terms.

GEOND lesson:

- GEOND should add a health check MCP tool or resource; `doctor` exists in CLI but not as a MCP tool.
- Concurrency tests should include simultaneous reservation and handoff writes.
- A small local SQLite or file-backed mode can improve first-run adoption, but should not weaken the Postgres-first evidence model.

## GEOND Benchmark Plan

### 1. Code Graph Correctness

Question: does GEOND return the right symbols, spans, and relationships?

Dataset:

- `examples/python_service`
- a mixed TypeScript/JavaScript fixture
- one pinned public repo with known ground truth

Metrics:

- symbol precision and recall
- caller/callee edge precision and recall
- import/export resolution accuracy
- patch hunk to symbol link accuracy
- indexing wall-clock time and entities/edges per second

Baseline comparisons:

- compare GEOND tree-sitter output to Remembrall-style impact queries where feasible
- compare GEOND line-range touched-symbol links to file-only links

Acceptance target:

- no regression in current fixtures
- line-range links must beat file-path links on touched-symbol precision
- deletion-only hunks must preserve old-line evidence without producing false current-line claims

### 2. Reservation And Conflict Prevention

Question: does dependency-aware reservation prevent work collisions earlier than file locks?

Dataset:

- synthetic two-agent scenarios over Python and TypeScript fixtures
- cases where two agents edit different functions in the same file
- cases where two agents edit different files connected by calls/imports

Metrics:

- conflict precision and recall
- false block rate
- advisory warning usefulness
- strict policy block correctness
- override-with-reason audit completeness

Baselines:

- file-only reservation
- symbol-only reservation
- dependency-expanded symbol reservation

Acceptance target:

- dependency-expanded reservations should catch cross-file caller/callee conflicts that file locks miss
- same-file independent symbols should be allowed under advisory mode and explain why they are independent

### 3. Handoff Package Quality

Question: can a second agent start safely from a GEOND handoff without re-reading the full session?

Dataset:

- three task families: feature implementation, bug fix, and test generation
- each has a first-agent handoff and a second-agent follow-up task

Metrics:

- second-agent time to first correct action
- number of extra file reads/tool calls before acting
- handoff evidence completeness
- missing risk count
- whether reservations are released/renewed correctly

Baselines:

- plain markdown handoff
- GEOND structured handoff without evidence refs
- GEOND structured handoff with patch, symbol, reservation, and test evidence refs

Acceptance target:

- structured evidence handoffs should reduce repeated context reads and preserve why the code changed.

### 4. Retrieval And Memory Quality

Question: does GEOND retrieve the right development evidence across agents and providers?

Dataset:

- existing `examples/benchmarks/*.json`
- expanded Korean/English mixed judgments
- imported VS Code, Codex, and Claude fixtures

Metrics:

- recall_at_k
- MRR
- nDCG_at_k
- rank movement after rerank
- latency percentiles
- provider request count, token count, and estimated cost when available

Baselines:

- keyword
- vector
- hybrid
- hybrid plus local rerank
- hybrid plus API rerank

Acceptance target:

- hybrid should dominate keyword-only on multilingual and semantic queries
- local-only privacy mode must block cloud calls before network access

### 5. MCP Contract And Interop

Question: can MCP clients consume the same evidence and coordination state consistently?

Dataset:

- direct Python FastMCP introspection
- sample MCP client configs under `examples/mcp_clients`
- synthetic tool calls for resources and resource templates

Metrics:

- tool count and resource-template count
- schema stability for `geond.evidence.v1`
- JSON serializability
- backward compatibility aliases
- error shape consistency

Acceptance target:

- `doctor` must report static resources and resource templates separately
- MCP tools that return evidence must include canonical evidence refs

### 6. Cloud Team Collaboration

Question: does GEOND still work when multiple machines share the same database and gateway?

Dataset:

- Windows client plus MacBook client
- Azure Database for PostgreSQL Flexible Server
- optional APIM gateway for embeddings/model calls

Metrics:

- setup time
- cross-machine handoff visibility latency
- reservation conflict visibility latency
- database connection stability
- Azure cost ledger inputs
- cleanup verification

Acceptance target:

- two clients should share reservations and handoffs without copying local files
- all cloud validation resources must be tagged and deleted after the run

## Immediate Benchmark Work Items

1. Add `examples/benchmarks/code_graph_ground_truth.json` for symbol and edge expectations.
2. Add `benchmark-code-graph` CLI for symbol/edge/link precision and recall.
3. Add `benchmark-coordination` CLI for reservation and handoff scenarios.
4. Add MCP contract checks for resource templates, not only static resources.
5. Add a saved benchmark report section for provider cost dimensions.
6. Add a two-client Azure Postgres collaboration smoke script with cleanup evidence.

