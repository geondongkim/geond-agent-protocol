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

## Priority 1: Evidence Quality

| Improvement | Why | Candidate implementation |
| --- | --- | --- |
| Patch hunk to symbol linking | File-level links are useful but coarse. | Implemented for unified diff new-line ranges, deletion-only hunks, and TypeScript/JavaScript body-span matching. |
| Canonical evidence schema | MCP clients should not reverse-engineer each response shape. | Implemented `geond.evidence.v1` with `target_id`, `locator`, `metadata`, and compatibility aliases for messages, snapshots, changesets, and symbols. |
| Explain-change synthesis | The current tool returns evidence, not a narrative. | Implemented as a deterministic, template-driven summary that cites `geond.evidence.v1` refs, available via `explain_change(include_narrative=True)`, the new `get_changeset_detail` MCP tool, and the `geond summarize-changeset` / `geond explain-change --narrative` CLI commands. |
| Cross-file call edges | Current call edges are strongest inside a single file. | Resolve imports and exported symbols across Python and TypeScript packages. |

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
| Reranking | Hybrid reciprocal rank is simple but not always precise. | Add optional local or API reranker stage after keyword/vector candidate generation. |
| Multilingual corpus | Korean and English queries are core to the project story. | Expand fixtures with multilingual messages, symbols, and expected evidence judgments. |
| Token and request accounting | Cost estimates need provider usage dimensions. | Capture token/request usage when providers or gateways expose it. |

## Priority 4: Agent Coordination

| Improvement | Why | Candidate implementation |
| --- | --- | --- |
| Lease renewal | Long-running agents need to extend reservations safely. | Add `renew_reservation` for files and symbols with audit entries. |
| Conflict policy levels | Some conflicts are warnings; others should block. | Add workspace policy for advisory, strict, and override-with-reason modes. |
| Handoff templates | Handoffs become better when they are structured. | Support templates for summary, tested commands, remaining risks, and next action. |
| Session lineage | Multi-agent workflows need provenance. | Link sessions, handoffs, actions, changesets, and benchmark runs into a navigable graph. |

## Priority 5: Packaging And Adoption

| Improvement | Why | Candidate implementation |
| --- | --- | --- |
| SQLite prototype | Postgres is powerful but heavy for first contact. | Provide a limited local SQLite mode for keyword search, imports, and MCP basics. |
| TypeScript SDK | Many MCP and editor integrations are TypeScript-first. | Add a thin client package for writing memories and querying evidence refs. |
| Release automation | Public OSS needs predictable artifacts. | Add GitHub Actions for tests, lint, package build, docs link checks, and release notes. |
| Example integrations | Users learn faster from concrete adapters. | Add examples for Continue, Claude Desktop, VS Code MCP, and a simple CI benchmark job. |

## Current Recommendation

MCP contract testing and narrative synthesis have landed: see
`tests/test_mcp_evidence_contract.py` (asserts `geond.evidence.v1` on every
tool that returns evidence) and `src/geond/retrieval/narrative.py`
(deterministic citation-bearing summaries used by `explain_change` and
`get_changeset_detail`). The next slice should focus on **cross-file call
edges** so narratives can describe upstream and downstream impact, and on
**agent-collaboration ergonomics** — see [`docs/agent_collaboration.md`](agent_collaboration.md)
for the questions the protocol still needs to answer.
