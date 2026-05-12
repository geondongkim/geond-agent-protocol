# Improvement Backlog

This backlog turns the current MVP into a stronger public protocol. It is
organized by product risk rather than by implementation convenience.

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

## Priority 5: Packaging And Adoption

| Improvement | Why | Candidate implementation |
| --- | --- | --- |
| SQLite prototype | Postgres is powerful but heavy for first contact. | Provide a limited local SQLite mode for keyword search, imports, and MCP basics. |
| TypeScript SDK | Many MCP and editor integrations are TypeScript-first. | Add a thin client package for writing memories and querying evidence refs. |
| Release automation | Public OSS needs predictable artifacts. | Add GitHub Actions for tests, lint, package build, docs link checks, and release notes. |
| Example integrations | Users learn faster from concrete adapters. | Add examples for Continue, Claude Desktop, VS Code MCP, and a simple CI benchmark job. |

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
module is indexed.
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
Keyword, vector, and hybrid search now support optional deterministic local
reranking over expanded candidate pools, and `rerank=api` can call a configured
HTTP reranker with local-only privacy guards. Structured handoff templates now preserve
tested commands, remaining risks, and next action metadata, workspace lineage
graphs link sessions, actions, handoffs, changesets, and benchmark runs,
`review_workspace_context` compares upcoming work with loaded coordination context,
and reranked benchmarks now report top-result changes, rank movement, rerank
scores, and missing API scores. The next slice should add **LSP-backed references** where available and continue the
**agent-collaboration ergonomics** work described in
[`docs/agent_collaboration.md`](agent_collaboration.md).
