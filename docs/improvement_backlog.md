# Improvement Backlog

This backlog turns the current MVP into a stronger public protocol. It is
organized by product risk rather than by implementation convenience.

The current product direction emphasises dependency-aware reservations,
handoff packages, patch-to-symbol evidence, MCP contract health, agent activity
observability, two-client cloud collaboration, and benchmark evidence.

## Priority 0: Trust And Safety

| Improvement | Why | Candidate implementation |
| --- | --- | --- |
| Retention policies per adapter | Imported chat logs can be sensitive even after redaction. | Add workspace-level retention config for raw events, messages, snapshots, and embeddings. |
| Redaction review mode | Pattern redaction is useful but imperfect. | Add `geond audit-redaction` to report finding counts and sample-safe categories before import. |
| Secret-free benchmark artifacts | Public evidence should be safe by default. | Add an artifact sanitizer that rejects subscription IDs, tenant IDs, emails, and key-looking strings before writing docs. |
| Keyless Azure path | API keys are acceptable for smoke tests but not ideal for production. | Make Entra ID auth the documented production default and add a managed-identity smoke variant. |
| Workspace identity guardrails | Folder moves and renames should not split agent memory. | Implemented `workspace_aliases`, alias-aware search/benchmark filters, MCP/CLI alias registration, git and manifest fingerprint suggestions, and suggestion explanations for ambiguous or partial matches. Next: package lock drift heuristics. |

## Priority 1: Evidence Quality

| Improvement | Why | Candidate implementation |
| --- | --- | --- |
| Patch hunk to symbol linking | File-level links are useful but coarse. | Implemented for unified diff new-line ranges, deletion-only hunks, and TypeScript/JavaScript body-span matching. |
| Canonical evidence schema | MCP clients should not reverse-engineer each response shape. | Implemented `geond.evidence.v1` with `target_id`, `locator`, `metadata`, and compatibility aliases for messages, snapshots, changesets, and symbols. |
| Explain-change synthesis | The current tool returns evidence, not a narrative. | Implemented as a deterministic, template-driven summary that cites `geond.evidence.v1` refs, available via `explain_change(include_narrative=True)`, the new `get_changeset_detail` MCP tool, and the `geond summarize-changeset` / `geond explain-change --narrative` CLI commands. |
| Cross-file call edges | Current call edges are strongest inside a single file. | Python and TypeScript/JavaScript import-qualified call resolution, default-import and re-export barrel resolution, `get_symbol_context` caller/callee retrieval, and call-impact narratives are implemented. Next: LSP-backed references. |

## Priority 2: Deployment And Operations

| Improvement | Why | Candidate implementation |
| --- | --- | --- |
| Bicep deployment path | Manual CLI scripts are good for validation, IaC is better for repeatability. | Add `infra/` Bicep for Azure OpenAI, APIM, Key Vault, managed identity, and optional VM. |
| `azd` workflow | New contributors need one command after configuration. | Add `azure.yaml`, preflight checks, and a documented `azd provision --preview` path. |
| Cleanup guardrail | Temporary validation must never leave surprise cloud resources. | Add a post-run cleanup verifier that fails if tagged validation resource groups remain. |
| Cost ledger | Billing data arrives late, but SKU/runtime signals are available immediately. | Store cost signals in a local JSON schema and optionally export to benchmark metadata. |

## Priority 3: Retrieval And Benchmarks

| Improvement | Why | Candidate implementation |
| --- | --- | --- |
| Provider comparison matrix | Retrieval quality differs across OpenAI, Azure OpenAI, gateway, and local embeddings. | Run saved benchmark comparisons with the same judgment file and publish markdown reports. |
| Reranking | Hybrid reciprocal rank is simple but not always precise. | Implemented optional deterministic local and pluggable HTTP API reranking for keyword/vector/hybrid candidates. |
| Multilingual corpus | Korean and English queries are core to the project story. | Seed-level multilingual judgments are added. Next: expand fixtures with Korean/English mixed symbols and longer conversation evidence. |
| Token and request accounting | Cost estimates need provider usage dimensions. | Capture token/request usage when providers or gateways expose it. |

## Priority 4: Agent Coordination

| Improvement | Why | Candidate implementation |
| --- | --- | --- |
| Lease renewal | Long-running agents need to extend reservations safely. | Implemented `renew_reservation`, `renew_symbol_reservation`, matching CLI commands, and reservation audit events for create/renew/release/expire transitions. |
| File reservation CLI parity | Context review recommendations should be executable from the CLI. | Implemented `reserve-files` and `release-reservation` commands matching MCP file reservation tools. |
| Conflict policy levels | Some conflicts are warnings; others should block. | Implemented workspace policy for advisory, strict, and override-with-reason modes across file and symbol reservations. |
| Handoff templates | Handoffs become better when they are structured. | Implemented standard metadata templates for summary, tested commands, remaining risks, and next action. |
| Session lineage | Multi-agent workflows need provenance. | Implemented workspace lineage graphs linking sessions, handoffs, actions, changesets, and benchmark runs. |
| Context review loop | Agents should compare the next task with current intent, reservations, and handoffs before editing. | Implemented `review_workspace_context` and `geond review-context` to assess requested work against active reservations, open handoffs, and lineage matches. |

## Priority 4.5: Agent Activity Observability

| Improvement | Why | Candidate implementation |
| --- | --- | --- |
| Local agent dashboard | Humans and PM agents need to see which agent is doing what without reading raw MCP JSON. | `geond dashboard serve` now serves Mission Control tabs, horizontally expanding Agent Fleet lanes, session/message cards, reservations, handoff-board lanes, Code Risk evidence cards, Changesets review lanes, Graph node/edge drilldowns, lineage counts, Usage Evidence with conversation/work/validation refs, kind/agent/status-filtered Activity Timeline with related event detail panes, browser verification, and dashboard GIF assets; next add focused review filters. |
| Dashboard HTTP API | UI, PM agents, and orchestrators need a stable read model that is not tied to MCP resources. | Implemented `geond dashboard serve` with `/health`, `/api/workspaces/{id}/overview`, `/activity`, `/timeline`, `/lineage`, `/reservations`, `/handoffs`, `/usage`, `/code-risk`, and `/changesets`; browser smoke now verifies the read model through the actual UI, and next filters should stay covered by that script. |
| Normalized activity stream | `agent_actions`, reservations, handoffs, changesets, and benchmark runs currently require multiple queries. | Implemented as a read-only projection over existing tables for sessions, actions, reservations, reservation events, handoffs, changesets, and benchmark runs; next persist or cache only if dashboard polling needs it. |
| Agent lifecycle adapters | Real-time views need consistent lifecycle events from Codex, Claude Code, Copilot, and CLI workflows. | Add optional hook examples for session start/end, pre/post tool use, validation, compaction, and stop events. |
| PM/orchestrator read model | Future PM and orchestration agents need blocker, ownership, and readiness signals. | Provide prompts and dry-run CLI examples that summarize open handoffs, stale reservations, risky symbols, and latest validation evidence. |

## Priority 5: Packaging And Adoption

| Improvement | Why | Candidate implementation |
| --- | --- | --- |
| SQLite prototype | Postgres is powerful but heavy for first contact. | Provide a limited local SQLite mode for keyword search, imports, and MCP basics. |
| TypeScript SDK | Many MCP and editor integrations are TypeScript-first. | Add a thin client package for writing memories and querying evidence refs. |
| Release automation | Public OSS needs predictable artifacts. | GitHub Actions now runs lint, compile, docs link checks, release notes preview, tests, benchmark smoke, package build, artifact uploads, tag-triggered GitHub Release creation, source/wheel/checksum release attachments, Sigstore keyless signing bundles, and manual PyPI trusted publishing; next add TestPyPI dry-run or publish observation after the first tag. |
| Example integrations | Users learn faster from concrete adapters. | Claude Desktop, Continue, VS Code MCP, VS Code LSP collection task, Continue LSP pre-query workflow, CI benchmark smoke, and `geond install` preview/write flows are documented; next add editor extension commands. |
| Apple Silicon onboarding | Contributors may clone on M-series MacBooks. | Native arm64 setup notes are documented; next validate on real hardware and add macOS CI if needed. |
| One-shot installer for coding agents | `examples/mcp_clients/*` exists but each agent must be wired manually. | Implemented `geond install` for VS Code MCP, VS Code LSP task, Claude Desktop, and Continue preview/write flows. Next: client detection, Codex/Claude Code hooks, and editor-extension commands. |
| Graph query DSL | `get_symbol_context` answers one symbol at a time; transitive/structural questions still require multiple tool calls. | Add a constrained, safe graph query (e.g. a typed predicate API) returning canonical `geond.evidence.v1` refs. |
| IaC and HTTP route nodes | Patch evidence often crosses code and deploy boundaries (Dockerfile, Kubernetes, route handlers). | Add IaC parsers (Docker/K8s/Kustomize) and cross-service HTTP route edges as new code graph node/edge types. |
| Trace and dead-code analyses | GEOND stores call edges but does not expose transitive call paths or reachability-based dead-code detection. | Add `trace_call_path` and reachability-based dead-symbol reports as MCP/CLI tools. |
| CLI option consistency | Coordination commands work, but related commands use slightly different option names and output conventions. | Normalize agent/workspace/output flags across reservation and handoff commands, then add CLI contract snapshots for common flows. |

## Current Recommendation

MCP contract testing, narrative synthesis, cross-file code graph edges, and
call-impact retrieval have landed: see
`tests/test_mcp_evidence_contract.py` (asserts `geond.evidence.v1` on every
tool that returns evidence) and `src/geond/retrieval/narrative.py`
(deterministic citation-bearing summaries used by `explain_change` and
`get_changeset_detail`). Python and TypeScript/JavaScript indexing now resolves
calls through relative, named, namespace, and absolute imports, storing `calls`
edges with `resolution=import_qualified_name_match`; `get_symbol_context`
returns those edges as `callers` and `callees`, and default imports plus
re-export barrel modules now resolve to source functions/classes when the target
module is indexed. Geond also accepts editor-provided LSP reference imports via
the CLI/MCP surface and exposes those `references` edges in symbol context. The
CLI now normalizes VS Code/LSP `Location[]` payloads into that import schema,
with `normalize-lsp-references` for dry runs and
`examples/lsp_references/vscode_locations.json` covering the fixture contract.
The `collect-lsp-references` CLI can now call a supplied stdio language server,
auto-select `pyright` or `typescript` profiles, write the live
`textDocument/references` Location payload, and optionally import the normalized
references in one step. VS Code task and Continue pre-query workflow examples
show how editors can prefill the target file, line, character, and server
profile. CI now uploads release notes, package distributions, checksums, and
sample benchmark artifacts; tag pushes create GitHub Releases from generated
notes and attach source/wheel/checksum files with Sigstore keyless signing
bundles; manual PyPI trusted publishing can publish a selected tag after the
PyPI trusted publisher is configured; and `geond install` previews or writes
common MCP/editor config files. The first dashboard read model is now available
through CLI/MCP: `dashboard-overview`, `dashboard-events`,
`get_dashboard_overview`, `get_agent_activity_events`, and
`geond://workspaces/{id}/activity`, and `geond dashboard serve` exposes the
same read model over localhost HTTP with Mission Control, horizontal Agent Fleet
lanes, session/message cards, and Activity Timeline. The next product slice is
richer dashboard views plus PM/orchestrator examples on top of those payloads.
Change narratives cite `code_edge` evidence when touched symbols have call
impact. Changeset detail lookup rejects ambiguous git commit prefixes with
explicit candidate matches. Reservation renewal is available for file and symbol
leases through MCP and CLI, reservation audit events now record create, renew,
release, and expiry transitions, and workspace conflict policy can keep
conflicts advisory, block them strictly, or require an explicit override reason.
Workspace aliases now preserve memory across folder moves, and keyword search
uses Postgres full-text plus `pg_trgm` substring matching before hybrid vector
merge. Git and manifest fingerprints can now suggest a likely alias before a
moved folder is registered, explain ambiguous or partial matches, and benchmark
reports resolve aliases as well.
Apple Silicon setup notes now document native arm64 tooling, Docker Desktop,
pgvector image behavior, Rosetta pitfalls, and local `.claude/` ignore handling.
CI notes now document why `GEOND_PRIVACY_MODE` should not be set globally in
workflow env, and tests that exercise cloud provider wiring explicitly set their
privacy mode.
Keyword, vector, and hybrid search now support optional deterministic local
reranking over expanded candidate pools, and `rerank=api` can call a configured
HTTP reranker with local-only privacy guards. Structured handoff templates now preserve
tested commands, remaining risks, and next action metadata, workspace lineage
graphs link sessions, actions, handoffs, changesets, and benchmark runs,
`review_workspace_context` compares upcoming work with loaded coordination context,
and reranked benchmarks now report top-result changes, rank movement, rerank
scores, and missing API scores. The next slice should add a **CI benchmark job** and continue the
**agent-collaboration ergonomics** work described in
[`docs/agent_collaboration.md`](agent_collaboration.md).
