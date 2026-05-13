# Patent-Driven Product Roadmap

The patent draft clarified the strongest product thesis:

> GEOND is not just memory, code search, AST analysis, handoff, or locking. Its
> main value is the pipeline that links AST-derived symbol dependency scope,
> diff hunk evidence, reservations, structured handoffs, and follow-up agent
> verification.

## Strategic Direction

GEOND should develop as a local-first protocol layer for code-aware agent
coordination. It should stay compatible with Codex, Copilot Chat, Claude Code,
Continue, and other MCP clients rather than becoming a full agent runtime.

The product should make three claims measurable:

1. A second agent can understand why code changed without replaying the whole chat.
2. Agents can reserve the symbols and dependency ranges they intend to touch.
3. Handoffs carry enough patch, test, risk, and reservation evidence for safe continuation.

## Next Implementation Tracks

### Track 1: Dependency-Aware Reservations

Goal:

Use the code graph to expand a requested reservation from a file or symbol into
a reasoned dependency scope.

Work:

- Add a `reservation_scope` concept that records direct targets and graph-expanded targets separately.
- Add `reserve-symbols --include-callers`, `--include-callees`, and `--depth`.
- Store scope explanation metadata: why each symbol was included, edge type, depth, and source evidence ref.
- Add conflict summaries that say whether a conflict is direct, caller/callee, import/export, test-related, or file-only.
- Add tests where file locks miss a cross-file conflict but dependency-aware reservation catches it.

Acceptance:

- `review-context` can explain not only that a conflict exists, but why the graph says the work overlaps.

### Track 2: Handoff Packages

Goal:

Upgrade handoff summaries into portable work packages.

Work:

- Add `handoff_packages` or structured metadata versioning inside `handoff_summaries`.
- Include intended files, symbols, reservation ids, changeset ids, tested commands, benchmark runs, and evidence refs.
- Add `consume-handoff` CLI/MCP tool that returns context review plus next recommended actions.
- Add close semantics that verify related reservations are released, renewed, or intentionally transferred.
- Add a package export format for external agents that do not use GEOND directly.

Acceptance:

- A follow-up agent can call one tool and receive intent, changed symbols, tests, risks, conflicts, and release instructions.

### Track 3: Patch-To-Symbol Evidence Quality

Goal:

Make "why did this symbol change?" precise enough for reviews and patent-grade demos.

Work:

- Expand ground-truth fixtures for mixed Python, TypeScript, JavaScript, and deletion-only diffs.
- Add confidence scoring that distinguishes exact body overlap, signature-only overlap, deletion anchor, and file fallback.
- Add narrative templates that cite hunk metadata and call-impact edges together.
- Add regression tests for renamed symbols and moved files.

Acceptance:

- `summarize-changeset` should prefer exact touched symbols and clearly label fallback links.

### Track 4: MCP Contract And Health

Goal:

Make MCP behavior easy to verify across clients.

Work:

- Add a MCP `health_check` tool mirroring CLI `doctor`.
- Add contract tests for all tools and resource templates.
- Report static resources and parameterized resource templates separately.
- Add example client tests for Claude Desktop, Continue, VS Code MCP, and Codex-style MCP configuration.

Acceptance:

- `doctor` and MCP contract tests agree on tool/resource/template counts.

### Track 5: Collaboration Deployment

Goal:

Prove GEOND works for a small team, not only one local machine.

Work:

- Add Bicep or Terraform for Azure Database for PostgreSQL Flexible Server, optional APIM, Key Vault, and managed identity.
- Add a Windows plus MacBook validation script that records cross-client reservation and handoff visibility.
- Add cost ledger capture for database SKU, runtime, APIM tier, model calls, VM runtime, and cleanup timestamps.
- Add cleanup verification that fails if tagged resource groups remain.

Acceptance:

- A temporary cloud run produces sanitized evidence and deletes all resources.

### Track 6: Benchmark Evidence

Goal:

Turn product claims into repeatable benchmark reports.

Work:

- Add code graph correctness benchmark.
- Add reservation conflict benchmark.
- Add handoff package benchmark.
- Add retrieval/provider comparison reports.
- Add "with GEOND vs without GEOND" agent-task A/B harness.

Acceptance:

- Public claims should name the benchmark surface precisely: retrieval, graph correctness, coordination safety, or agent productivity.

## Recommended Order

1. Fix small validation quality gaps found by `doctor` and MCP introspection.
2. Build MCP contract tests for resource templates.
3. Implement dependency-aware reservation scopes.
4. Implement handoff package consumption and reservation transfer semantics.
5. Add code graph and coordination benchmarks.
6. Validate two-client Azure Postgres collaboration.
7. Produce a public demo that shows graph-derived reservation, patch evidence, and handoff continuation in one story.

## Current Validation Notes

2026-05-13 local checks:

- `uv run geond --help` lists current CLI surfaces including parsing, indexing, reservation, handoff, benchmark, changeset, and symbol commands.
- `uv run geond doctor --format json` returned status `ok`; Postgres, pgvector, Docker, env, and MCP import were reachable.
- `uv run python -m compileall src` passed.
- A sample workspace was seeded, reviewed with `review-context`, assigned a symbol reservation, given a structured handoff, benchmarked with keyword search, and purged successfully.

Issues found:

- `doctor` previously reported only static MCP resources and omitted parameterized resource templates. This was corrected to report tools, static resources, and resource templates separately.
- The seeded sample benchmark query `build_answer` returned zero keyword results. This is not a runtime failure, but the seed data is too small for roadmap benchmark smoke assertions. Future smoke tests should query known seeded text or seed a benchmark fixture first.

