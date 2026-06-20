# Geond Orchestrator Split Boundary

This note marks the intended boundary between the public `geond-agent-protocol`
substrate and a future private `Geond Orchestrator` package or repository. The
code is now in the first same-repo namespace split:

- Public protocol and MCP substrate live under `src/geond/`.
- Controller policy lives under `src/geond_orchestrator/`.
- Legacy `geond.orchestrator_*` modules remain as temporary compatibility
  wrappers that alias the canonical `geond_orchestrator.*` modules.

The current goal is to keep imports and contracts clear enough that the
controller package can move to a separate repository without changing the
protocol shape.

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

## Operator CLI

The recommended operator entrypoint is:

```bash
geond orch status <run_id>
geond orch plan --workspace <workspace-uri> --run <run_id>
geond orch agent <run_id>
geond orch worker claim --run <run_id> --agent <agent>
geond orch worker finish <lease_id> --summary "<summary>"
geond orch action list --workspace <workspace-uri> --run <run_id>
geond orch scheduler plan --workspace <workspace-uri> --run <run_id>
geond orch ci watch <github-run-id> --repo <owner/repo> --exit-status
```

The longer command remains available during the transition:

```bash
geond-orchestrator status <run_id>
```

Both entrypoints route to the same implementation today. After a physical repo
split, `geond orch` should delegate to an installed `geond-orchestrator`
package or print a clear install/help message when the package is unavailable.

## Private Orchestrator Candidates

These pieces are candidates for a future private `Geond Orchestrator` package:

- Agent Mode control-loop policy and action priority rules.
- LLM planner prompts, output-repair policy, and planner-agent selection.
- Planner review-gate policy for deciding whether generated task graphs are
  safe to materialize.
- Operator action-bundle composition and future command queue policy.
- Human-approved operator action queue and typed action execution policy.
- Workspace-level scheduler, action budget guard, and worker pooling policy.
- Usage-aware budget guards, cost forecast policy, and scheduler enforcement.
- Local background daemon loop, lock policy, and execution cadence.
- Spawn policy for Codex, Claude, and future worker adapters.
- Verifier-session spawn policy, including read-only review prompts, tool
  restrictions, and controller handoff rules.
- Product UX beyond the read-only Dashboard protocol views.
- Git/PR finalize policy, release gates, and organization-specific approval
  behavior.
- Multi-run scheduling, budgeting, worker pooling, and hosted orchestration
  service code.

These modules currently live in this repository to keep the protocol evolution
fast, under `src/geond_orchestrator/`, but they should avoid leaking private
policy into storage schemas or MCP contracts.

## Current Import Boundary

Protocol-oriented modules should not depend on private control policy:

- `geond.storage.orchestration`
- `geond.degraded_ledger`
- `geond.task_graph`
- `geond.mcp_server.server`

MCP may call controller behavior through a narrow bridge module, but MCP server
modules should not directly import private controller candidates.

Controller-oriented modules may compose protocol primitives:

- `geond_orchestrator.orchestrator`
- `geond_orchestrator.orchestrator_planner`
- `geond_orchestrator.orchestrator_control`
- `geond_orchestrator.orchestrator_task_planner`
- `geond_orchestrator.orchestrator_llm_planner`
- `geond_orchestrator.orchestrator_graph_review`
- `geond_orchestrator.orchestrator_worker_review`
- `geond_orchestrator.orchestrator_action_bundle`
- `geond_orchestrator.orchestrator_action_queue`
- `geond_orchestrator.orchestrator_scheduler`
- `geond_orchestrator.orchestrator_budget`
- `geond_orchestrator.orchestrator_daemon`
- `geond_orchestrator.orchestrator_spawn`

Dashboard read models may summarize controller artifacts, but they must remain
read-only and redacted. Raw prompts, stdout, stderr, and full worker logs should
stay local artifacts unless an explicit review workflow asks for them.

## Boundary Tests

The split boundary is enforced by tests that check:

- Protocol storage, task graph, degraded ledger, and MCP modules do not import
  controller policy modules directly.
- MCP orchestration preview tools go through the narrow bridge facade.
- Canonical controller modules under `geond_orchestrator` do not import the
  legacy `geond.orchestrator_*` wrappers.
- The `geond-orchestrator` console script points at
  `geond_orchestrator.cli:main`.
- Temporary legacy wrappers alias the canonical modules during the transition.

## Next Split Phases

1. Stabilize the public protocol APIs consumed by the orchestrator: run/task,
   lease, worker, evidence, decision, finding, readiness, task graph, degraded
   ledger, and dashboard read-model contracts.
2. Release `geond-agent-protocol` with the stable contracts, compatibility
   wrappers, and `geond orch` delegation behavior.
3. Create the `geond-orchestrator` repository/package and move
   `src/geond_orchestrator/`, controller CLI tests, planner prompts, worker
   templates, and orchestration playbooks there.
4. Make `geond-orchestrator` depend on the released `geond-agent-protocol`
   package instead of private in-repo imports.
5. Remove old `geond.orchestrator_*` wrappers after downstream users migrate.

## Split Criteria

A private split becomes worthwhile when at least two of these are true:

- Orchestrator policy changes faster than the public protocol.
- Hosted or organization-specific UX needs private release cadence.
- Worker-spawn adapters require secrets, paid routing, or proprietary prompts.
- Multiple products consume the same protocol but need different control loops.
- Public MCP compatibility work is slowed down by private product concerns.

Until then, keep the current repo modular and test the boundary with service
functions rather than moving files prematurely.
