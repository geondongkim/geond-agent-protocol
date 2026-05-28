# Geond Open-Source Marketing Strategy

Last updated: 2026-05-29 KST

This document turns the README rewrite, demo GIFs, learning notebooks, open-source
readiness notes, and the latest external launch research into a concrete
marketing plan for Geond.

## Source Inputs

- Current repository state: multilingual README, pair-coding demo GIFs, tutorial
  notebooks, MCP/CLI/dashboard docs, Azure shared PostgreSQL validation, and
  Codex + Antigravity verification.
- Existing strategy: earlier `docs/marketing_strategy.md`, which focused on
  conservative directory submission and launch hygiene.
- Attached strategy files:
  - `Geond Agent Protocol_ Manus 마케팅 전략 제안서.md`
  - `[실행 지침서] Geond Agent Protocol 오픈소스 마케팅 전략 수립 및 보완안.md`
- Launch references:
  - [GitHub repository topics](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/classifying-your-repository-with-topics)
  - [Hacker News Guidelines](https://news.ycombinator.com/newsguidelines.html)
  - [GeekNews](https://news.hada.io/)
  - [Awesome MCP Servers](https://github.com/punkpeye/awesome-mcp-servers)
- Execution companion: [docs/marketing_agent_execution_plan.md](marketing_agent_execution_plan.md)
- International launch channels:
  [docs/international_launch_channels.md](international_launch_channels.md)

## Executive Decision

Geond should stop leading with the abstract phrase "shared context layer" in
marketing channels. That phrase is accurate, but it does not create instant
developer pull.

Lead instead with a concrete pain:

> Stop AI coding agents from stepping on each other's work.

Then explain the mechanism:

> Geond is a local-first MCP server and CLI that gives Copilot, Codex, Claude
> Code, Antigravity, Manus, and custom agents shared memory, file/symbol
> reservations, handoffs, code graph evidence, and a reviewer dashboard.

Use "coordination substrate" carefully. Do not imply that Geond runs the agents.
Geond does not replace Codex, Antigravity, Copilot, Claude Code, or Manus. It is
the shared evidence and coordination layer those tools can use.

## Public Claim Boundary

Safe to claim today:

- local-first CLI and stdio MCP server
- PostgreSQL/pgvector storage
- repository-centered memory and hybrid search
- Copilot Chat, Codex, Claude Code, Antigravity, and Manus import paths
- redaction before persistence
- Python/TypeScript/JavaScript code graph indexing
- file and symbol reservations with audit events
- structured handoffs and changesets
- dashboard views for Mission Control, sessions, handoffs, code risk, timeline,
  lineage, reservations, and usage evidence
- local processes pointed at shared Azure/remote PostgreSQL for team mode
- multilingual README and notebook-based onboarding

Do not present as fully implemented:

- complete enterprise IAM, RLS, or dedicated MCP audit streaming
- production SaaS hosting
- broad non-development SaaS adapters
- automatic dependency-expanded reservations across every graph edge
- vendor-stable guarantees for private Copilot/Codex/Claude storage formats
- Geond as an agent runner or replacement for orchestrators

Patent wording should stay out of the README front matter until legal and
international filing strategy are confirmed. If a patent notice is used later,
keep it short and avoid revealing deeper implementation details not already in
the public code.

## Positioning House

| Layer | Copy |
| --- | --- |
| Hook | Stop AI coding agents from stepping on each other's work. |
| One-liner | Geond gives multiple AI agent tools shared memory, reservations, handoffs, and review evidence for the same repo. |
| Category | Local-first MCP coordination server for multi-agent development workflows. |
| Short pitch | Use Codex, Claude Code, Copilot, Antigravity, Manus, or custom MCP agents without losing who changed what, why it changed, what is reserved, and what the next agent should do. |
| Technical proof | PostgreSQL/pgvector, MCP, CLI, code graph, reservations, handoffs, dashboard, Azure shared DB validation. |
| Trust boundary | Alpha, repository-centered today, local-first by default, sanitized demos only. |

## Target Audiences

| Priority | Audience | Pain | Message | Primary proof |
| --- | --- | --- | --- | --- |
| 1 | Multi-agent coding power users | Two or more AI tools touch the same repo and lose context. | Geond gives agents a shared work memory and reservation system. | Pair-coding GIF, `review-context`, reservations, handoffs. |
| 2 | MCP early adopters | MCP servers are useful but often stateless or isolated per client. | Geond is a durable memory and coordination MCP server. | MCP smoke, client config docs, compact evidence refs. |
| 3 | Platform/infra engineers | Teams need visibility, auditability, and shared DB options without SaaS lock-in. | Keep agents local while sharing PostgreSQL-backed evidence. | Azure validation, dashboard DB source badge, usage evidence. |
| 4 | PM/review/security users | Raw transcripts are unreadable and hard to review. | Dashboard turns agent work into sessions, timeline, risk, lineage, and handoffs. | Review loop GIF, dashboard docs. |
| 5 | OSS contributors | Heavy setup and unclear scope reduce contribution. | The README and notebooks make the first workflow concrete. | Quick start, learning notebooks, docs link checks. |

## Differentiation

| Alternative | What it is good at | Where Geond differs |
| --- | --- | --- |
| Mem0 / Zep-style memory | Conversation memory, user facts, long-term recall. | Geond is repo/work evidence centered: files, symbols, changesets, reservations, handoffs, dashboard review. |
| Generic vector RAG MCP | Search over docs or files. | Geond stores agent sessions, work intent, code graph links, and coordination state, not just chunks. |
| Git and git worktree | Source-of-truth diffs and isolated branches. | Git tells what changed; Geond adds why, who claimed what, current handoffs, and agent-readable evidence refs. |
| Agent orchestrators | Running or sequencing agents. | Geond does not run agents; it gives separate tools a shared state layer. |
| Project management tools | Human task tracking. | Geond captures machine-readable evidence from agent work and exposes it through MCP/CLI/dashboard. |

## Launch Readiness Gates

Before a broad launch:

- `uv run python scripts/check_docs_links.py` passes.
- `uv run geond doctor --format text` passes from a fresh setup.
- `uv run geond mcp-smoke --format text --strict` passes.
- README first viewport shows the language selector, value prop, and main GIF.
- Demo GIFs are scrubbed and generated from sanitized text.
- `docs/patent`, `repo`, `tmp`, `result`, `results`, and generated videos are
  not staged or referenced as public artifacts.
- GitHub repository topics are added. GitHub topics are public discovery labels,
  limited to 20 topics, and should use lowercase letters, numbers, and hyphens.
- Issue templates and PR template exist or the README clearly says alpha support
  is lightweight.
- Release/tag plan is decided, or public copy says "install from source".

Recommended GitHub topics:

```text
mcp, model-context-protocol, ai-agents, agent-memory, multi-agent,
local-first, postgresql, pgvector, code-graph, developer-tools,
agent-coordination, ai-coding, copilot, claude-code, codex
```

## Launch Plan

### Phase 0: Package the Proof

Goal: make the repository explain itself in under one minute.

- Keep the README hero focused on shared memory and agent coordination.
- Add one sharper "collision prevention" GIF or short video:
  1. Agent A reserves a file or symbol.
  2. Agent B checks context and sees the reservation.
  3. Agent B records a handoff or chooses a non-conflicting target.
  4. Reviewer sees the evidence trail in the dashboard.
- Add a `docs/comparison.md` page for Mem0/Zep/generic RAG/git worktree/Geond.
- Add a concise architecture image derived from the current Mermaid flow, not
  from private patent drawings.
- Add GitHub social preview image using the same visual language as the GIFs.

### Phase 1: MCP Directory Submission

Goal: be findable where MCP users already search.

Primary target: Awesome MCP Servers.

Suggested entry:

```markdown
- [Geond Agent Protocol](https://github.com/geondongkim/geond-agent-protocol) - Local-first MCP server for multi-agent repo coordination: shared memory, code evidence, file/symbol reservations, handoffs, and dashboard review backed by PostgreSQL.
```

PR body:

```markdown
Geond is an alpha local-first MCP server and CLI for teams using multiple AI
agent tools on the same repository. It imports Copilot Chat, Codex, Claude Code,
Antigravity, and Manus evidence; stores redacted memory in PostgreSQL/pgvector;
indexes code graph context; exposes reservations and handoffs through MCP; and
serves a read-only dashboard for review. It can run fully local or point local
agent processes at a shared PostgreSQL-compatible database such as Azure
Database for PostgreSQL.
```

Categories to try first: Memory/Knowledge and Developer Tools.

### Phase 2: Show HN

Goal: get technical feedback, not just stars.

HN guidelines favor intellectually interesting submissions and discourage
attention-grabbing title tricks such as uppercase, exclamation points, and
salesy claims. Keep the title plain.

Best title:

```text
Show HN: Geond - shared memory and reservations for AI coding agents
```

Backup titles:

```text
Show HN: Geond - local-first coordination for Codex, Claude Code and Copilot
Show HN: Geond - an MCP server for multi-agent repo handoffs
```

First comment outline:

1. "I built this after using multiple AI coding agents on the same repo and
   losing context between sessions."
2. "Git is still the source of truth for diffs; Geond focuses on why the change
   happened, what is reserved, and what the next agent should know."
3. "It is local-first: CLI, MCP server, dashboard, PostgreSQL/pgvector. Shared
   DB mode is optional for teams."
4. "What works today: imports, search, code graph, reservations, handoffs,
   dashboard, Azure shared DB validation."
5. "What does not work yet: enterprise IAM/RLS/audit streams and broad SaaS
   adapters."
6. Ask for feedback on setup friction, MCP surface, and whether the reservation
   model matches real workflows.

### Phase 3: Reddit And Community Posts

Use different messages per community.

| Channel | Angle | Post title |
| --- | --- | --- |
| `r/mcp` | MCP contract, compact evidence refs, client config. | "I built a local-first MCP server for shared memory, reservations, and handoffs across coding agents" |
| `r/LocalLLaMA` | Local-first, redaction, PostgreSQL, no SaaS requirement. | "Local-first shared memory for AI coding agents, backed by PostgreSQL" |
| `r/ClaudeAI` | Claude Code import and pair-coding workflow. | "Sharing Claude Code context with Codex/Copilot through a local MCP server" |
| `r/vibecoding` or similar | Practical conflict story. | "What happens when two AI coding agents edit the same repo at once?" |

Do not post the same text everywhere. Each post should include one concrete
workflow and one ask for feedback.

### Phase 4: Korea And Localized Launches

Goal: use the multilingual README as an actual distribution asset, not just a
polish signal.

Start with GeekNews for Korea. GeekNews is the closest Korean launch analogue to
Hacker News for this project because it focuses on development, technology,
products, open source, and startup stories. Use a Korean title and comment, not
a direct English HN translation.

Then expand by language:

| Language | First channel | Launch style |
| --- | --- | --- |
| Korean | GeekNews | Korean link submission plus builder comment. |
| Japanese | Zenn, then Qiita | Technical article with setup, MCP config, and pair-coding workflow. |
| Simplified Chinese | V2EX, then Juejin or SegmentFault | Discussion-first post asking for workflow critique before article syndication. |
| Spanish | HackniA, then Menéame if there is a useful Spanish article | Maker/dev community feedback first; broad social-news link later. |
| French | LinuxFr.org | Open-source focused write-up with local-first and PostgreSQL details. |
| German | heise Developer, entwickler.de, Golem.de watchlist, plus German-speaking dev communities | German technical article or editorial pitch; emphasize privacy and engineering tradeoffs. |

Detailed channel notes, localized hooks, and the GeekNews draft are in
[docs/international_launch_channels.md](international_launch_channels.md).

### Phase 5: Technical Content Series

Publish three posts after the repo is ready enough for strangers to try.

1. "Why AI coding agents step on each other's work"
   - problem story, reservation model, handoff model, dashboard review
2. "Connecting Codex, Claude Code, Copilot, Antigravity, and Manus with MCP"
   - installation, importers, MCP client config, smoke test
3. "Why PostgreSQL is the backbone for agent coordination"
   - ACID reservation state, pgvector search, team DB mode, Azure validation

Each post should point back to a runnable notebook or exact command, not only to
the README.

## Funnel And Metrics

| Funnel stage | Metric | Conservative target, 4 weeks | Stretch target, 4 weeks |
| --- | --- | ---: | ---: |
| Awareness | GitHub stars | 100 | 500 if HN reaches front page |
| Awareness | Directory listings | 1 | 3 |
| Activation | Fresh-clone `doctor` success reports | 10 | 30 |
| Activation | First MCP smoke success | under 30 min | under 10 min |
| Engagement | Issues or discussions from external users | 5 | 20 |
| Trust | Privacy/security concerns resolved in docs | 100% triaged | 100% triaged |
| Contribution | External PRs | 1 | 5 |
| Learning | Notebook runs or questions | 5 | 20 |

Do not optimize only for stars. The best early signal is whether users can
complete the Quick Start and understand where Geond fits beside git and their
existing agent tools.

## Agent Execution Strategy: Codex vs Antigravity

For this marketing strategy and repository updates, Codex is the better primary
agent.

Why Codex should lead:

- It is already operating inside the Geond repository and can inspect current
  files, run link checks, edit docs, commit, and push.
- The strategy must stay aligned with implemented features, README claims,
  launch risk docs, and local validation artifacts.
- It can keep the claim boundary conservative and avoid accidentally exposing
  private patent or transcript material.

Where Antigravity is useful:

- Generate many campaign-copy variants once the claim boundary is fixed.
- Run broad web/community research prompts against public pages.
- Monitor communities or draft weekly opportunity reports.
- Act as a second reviewer for "would a developer understand this in 10
  seconds?" messaging.

Best workflow:

1. Codex owns source-of-truth docs, verification, commits, and public-claim
   guardrails.
2. Antigravity drafts alternate titles, community-specific copy, and monitoring
   reports.
3. Both write findings back into Geond via MCP/importers so the evidence trail
   remains shared.
4. Human approves any public post, directory PR, or patent-related wording.

## Immediate Backlog

| Priority | Task | Owner |
| --- | --- | --- |
| P0 | Add GitHub topics to repository metadata. | Human or GitHub CLI with token |
| P0 | Create `docs/comparison.md` with Mem0/Zep/generic RAG/git worktree comparison. | Codex |
| P0 | Add issue template and PR template with privacy/alpha checklist. | Codex |
| P1 | Generate "reservation prevents collision" README GIF or short video. | Codex |
| P1 | Add GitHub social preview image. | Codex |
| P1 | Draft Awesome MCP Servers PR body and checklist. | Codex, then human review |
| P1 | Draft Show HN title and first comment. | Codex + Antigravity variants |
| P1 | Draft GeekNews title/comment and localized channel sequence. | Codex, Korean human review |
| P2 | Draft 3-post technical content series. | Antigravity first draft, Codex fact check |
| P2 | Create community monitoring prompt/runbook for HN, GeekNews, Reddit, GitHub, and localized language channels. | Antigravity |
| P2 | Add FAQ from repeated issues after first external feedback. | Codex |

Completed on 2026-05-29:

- GitHub repository description and topics were updated with `gh`.
- [docs/comparison.md](comparison.md) was added.
- Issue templates and a PR template were added under `.github/`.
- Antigravity `agy` was used for bounded copy ideation; Codex reviewed and
  rejected over-broad wording before writing source-of-truth docs.
- [docs/international_launch_channels.md](international_launch_channels.md)
  was added to cover GeekNews and the README language launch lanes.

## Messaging Guardrails

- Say "prevents and surfaces conflicts" rather than "guarantees zero conflicts."
- Say "verified with Codex and Antigravity" as an example, not the product
  category.
- Say "shared PostgreSQL-compatible profile such as Azure PostgreSQL" rather
  than making Azure a required dependency.
- Keep "alpha" visible but pair it with concrete implemented proof.
- Keep private transcripts, patent drafts, local screenshots, and unpublished
  implementation details out of public collateral.

## Reusable Copy

Short repository description:

```text
Local-first MCP server for shared memory, reservations, handoffs, and review evidence across AI coding agents.
```

One-sentence pitch:

```text
Geond lets Codex, Claude Code, Copilot, Antigravity, Manus, and custom MCP agents share repo memory, reserve files or symbols, leave handoffs, and give reviewers one evidence trail.
```

Conservative alpha disclaimer:

```text
Geond is alpha and strongest today for repository-centered workflows. It is local-first by default, uses PostgreSQL/pgvector for durable memory, and treats enterprise IAM/RLS/audit streaming and broad SaaS adapters as roadmap work.
```

Community ask:

```text
If you use more than one AI coding tool on the same repo, I would love feedback on whether the reservation and handoff model matches the conflicts you actually hit.
```
