# Open Source Readiness

This document tracks the risks and launch work required to move Geond from a
personal alpha project into a credible open-source MCP and agent coordination
project.

## Current Position

Geond is ready to present as an alpha local-first shared context layer for
heterogeneous AI agents. The implemented surface is repository-centered:
Copilot Chat, Codex, Claude Code, MCP, CLI, PostgreSQL/pgvector, code graph,
reservations, handoffs, dashboard read models, usage evidence, and Azure-backed
shared database validation.

The broader product story is cross-functional: planning, product, design, QA,
marketing, support, operations, engineering, review, security, documentation,
deployment, and PM/orchestration agents should be able to share the same context
model as adapters are added.

## Launch Risks

| Risk | Why it matters | Mitigation |
| --- | --- | --- |
| Patent/public disclosure timing | README, docs, releases, demos, and marketplace submissions can become public disclosure evidence. | File or review patent strategy before major new disclosures. Preserve dates, URLs, commits, and scrubbed evidence. Keep local patent drafts out of git. |
| Apache-2.0 patent grant | Apache-2.0 includes a patent grant that may affect future enforcement strategy. | Review license and patent strategy with counsel before broad promotion or dual-license decisions. |
| Overclaiming | Cross-functional positioning can sound implemented even when current adapters are repository-centered. | Say "current implementation is strongest for repository workflows" and mark non-development SaaS adapters as roadmap. |
| Private transcripts | Agent logs may include secrets, customer data, or personal information. | Redact before persistence, use synthetic fixtures, document local-only defaults, and warn contributors not to commit raw exports. |
| Local-only folders | `docs/patent`, `repo`, `tmp`, `result`, `results`, and `videos` may contain private or third-party material. | Keep them ignored. Never force-add them. Add PR checklist reminders. |
| Third-party reference repos | `repo/` contains external OSS checkouts used for research. | Do not redistribute through this repo. Cite public upstreams only in docs. |
| Private storage formats | VS Code Copilot Chat, Codex, and Claude Code local formats may change and are not public stable APIs. | Treat importers as best-effort adapters, add fixture tests, and avoid promising vendor compatibility guarantees. |
| Enterprise IAM gaps | Shared DB mode is useful but not yet a full enterprise access-control story. | Document alpha status, DB-credential model, missing IAM/RLS/audit streams, and future work. |
| Cloud cost and cleanup | Azure validation can create paid resources. | Use tagged temporary resource groups, cleanup scripts, and sanitized validation artifacts. |
| Security reporting | Users need a non-public way to report vulnerabilities. | Maintain `SECURITY.md` and enable GitHub Private Vulnerability Reporting if available. |
| Setup heaviness | PostgreSQL/pgvector and Python/uv are heavier than one-shot MCP tools. | Keep quickstart short, improve `geond install`, consider limited SQLite mode, and publish clear tradeoffs. |
| Name/trademark | The project name may conflict later or be hard to pronounce. | Keep pronunciation note and run a trademark/search review before major branding. |
| Support burden | Users may expect production support from an alpha project. | Label alpha clearly, document known gaps, and keep issue templates focused. |

## OSS Hygiene Checklist

- [x] Apache-2.0 license present.
- [x] CI workflow present.
- [x] PyPI publish workflow present.
- [x] README rewritten for concise positioning and quickstart.
- [x] CONTRIBUTING guide added.
- [x] SECURITY policy added.
- [ ] Code of Conduct decision.
- [ ] Issue templates.
- [ ] PR template.
- [ ] Release tags and changelog after the next stable alpha slice.
- [ ] TestPyPI or publish observation after the first tag.
- [ ] GitHub Private Vulnerability Reporting enabled, if available.
- [ ] Repository topics added: `mcp`, `model-context-protocol`, `ai-agents`, `agent-memory`, `postgresql`, `pgvector`, `multi-agent`, `local-first`.

## Public Claims Boundary

Safe to claim today:

- local-first CLI and stdio MCP server
- PostgreSQL/pgvector storage
- VS Code Copilot Chat, Codex JSONL, and Claude Code JSONL importers
- redaction before persistence
- keyword/vector/hybrid search and evidence refs
- Python/TypeScript/JavaScript code graph indexing
- LSP reference import and diff hunk-to-symbol links
- file/symbol reservations, conflict policies, audit events, and handoffs
- read-only dashboard with DB source badge, agent lanes, sessions, timeline,
  lineage, reservations, handoffs, code risk, changesets, and usage evidence
- local processes pointed at shared Azure/remote PostgreSQL for team validation

Do not present as fully implemented yet:

- automatic dependency-expanded reservations for all graph edges
- dedicated non-development SaaS adapters
- enterprise IAM/RLS/audit streams
- complete token billing for every model/provider path
- production SaaS hosting
- vendor-stable Copilot/Codex/Claude storage APIs

## Pre-Launch Sequence

1. Keep patent/local evidence private and confirm filing strategy.
2. Run docs link checks and tests.
3. Ensure README, CONTRIBUTING, SECURITY, and roadmap language match actual code.
4. Add screenshots/GIFs that do not expose secrets.
5. Create a release tag and attach artifacts when package quality is acceptable.
6. Submit to MCP directories and Awesome MCP Servers with conservative alpha wording.
7. Track feedback and convert repeated questions into docs or good-first issues.
