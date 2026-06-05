# Geond Orchestrator Split Boundary

This note marks the intended boundary between the public `geond-agent-protocol`
substrate and a future private `Geond Orchestrator` package or repository. The
code is not split yet. The current goal is to keep imports and contracts clear
enough that a split can happen later without changing the protocol shape.

## Public Protocol Surface

These pieces should remain in `geond-agent-protocol`:

- MCP tools and resources that expose stable state contracts.
- SQL schemas and migrations for orchestration state.
- Storage functions for goal, run, task, worker, lease, evidence, approval,
  finding, decision, readiness, degraded ledger reconcile, and task graph state.
- Degraded ledger event format: `geond.degraded_ledger_event.v1`.
- Task graph input/output shape: keys, titles, priorities, dependencies,
  required evidence, and status.
- Read-only Dashboard Mission Control payloads, including redacted trace
  summaries, operator action bundles, local action queue summaries, and artifact
  paths.

The public surface should prefer deterministic payloads, stable error codes,
and CLI/MCP parity over product-specific policy.

## Private Orchestrator Candidates

These pieces are candidates for a future private `Geond Orchestrator` package:

- Agent Mode control-loop policy and action priority rules.
- LLM planner prompts, output-repair policy, and planner-agent selection.
- Planner review-gate policy for deciding whether generated task graphs are
  safe to materialize.
- Operator action-bundle composition and future command queue policy.
- Human-approved operator action queue and typed action execution policy.
- Spawn policy for Codex, Claude, and future worker adapters.
- Product UX beyond the read-only Dashboard protocol views.
- Git/PR finalize policy, release gates, and organization-specific approval
  behavior.
- Multi-run scheduling, budgeting, worker pooling, and hosted orchestration
  service code.

These modules currently live in this repository to keep the protocol evolution
fast, but they should avoid leaking private policy into storage schemas or MCP
contracts.

## Current Import Boundary

Protocol-oriented modules should not depend on private control policy:

- `geond.storage.orchestration`
- `geond.degraded_ledger`
- `geond.task_graph`
- `geond.mcp_server.server`

MCP may call controller behavior through a narrow bridge module, but MCP server
modules should not directly import private controller candidates.

Controller-oriented modules may compose protocol primitives:

- `geond.orchestrator`
- `geond.orchestrator_planner`
- `geond.orchestrator_control`
- `geond.orchestrator_task_planner`
- `geond.orchestrator_llm_planner`
- `geond.orchestrator_graph_review`
- `geond.orchestrator_action_bundle`
- `geond.orchestrator_action_queue`
- `geond.orchestrator_spawn`

Dashboard read models may summarize controller artifacts, but they must remain
read-only and redacted. Raw prompts, stdout, stderr, and full worker logs should
stay local artifacts unless an explicit review workflow asks for them.

## Split Criteria

A private split becomes worthwhile when at least two of these are true:

- Orchestrator policy changes faster than the public protocol.
- Hosted or organization-specific UX needs private release cadence.
- Worker-spawn adapters require secrets, paid routing, or proprietary prompts.
- Multiple products consume the same protocol but need different control loops.
- Public MCP compatibility work is slowed down by private product concerns.

Until then, keep the current repo modular and test the boundary with service
functions rather than moving files prematurely.
