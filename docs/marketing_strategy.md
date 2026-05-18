# Marketing And Submission Strategy

This document turns the README rewrite and open-source readiness work into a
launch plan for Geond.

## Positioning

One-line positioning:

> Geond is a local-first, cloud-capable shared context and coordination layer for
> heterogeneous AI agents, with MCP tools for memory, code graph evidence,
> reservations, handoffs, and dashboard visibility.

Short directory copy:

> Geond stores agent sessions, code/work evidence, reservations, handoffs, and
> dashboard read models in PostgreSQL so Copilot, Codex, Claude Code, MCP, PM,
> QA, security, documentation, deployment, and other agents can coordinate across
> sessions and machines.

Use this boundary in public copy:

- Implemented today: repository-centered agent memory, code graph, MCP, CLI,
  dashboard, reservations, handoffs, usage evidence, and shared PostgreSQL team
  validation.
- Roadmap: dedicated product/design/QA/marketing/support/operations adapters,
  enterprise IAM, row-level security, and dedicated MCP audit streams.

## Target Audiences

- AI agent builders who need persistent memory and evidence.
- MCP users looking for shared context across multiple clients.
- Engineering teams using Copilot, Codex, Claude Code, Continue, or custom MCP
  agents in the same repository.
- PM/QA/security/documentation/deployment users who need visibility into agent
  work without reading raw transcripts.
- Platform teams exploring local-first plus shared PostgreSQL collaboration.

## Launch Prerequisites

Before broad promotion:

- README is concise and current.
- CONTRIBUTING and SECURITY are present.
- Open-source readiness risks are documented.
- Patent filing/public disclosure strategy is clear.
- Demo GIFs and screenshots are scrubbed.
- `docs/patent`, `repo`, `tmp`, `result`, `results`, and `videos` are not staged.
- `uv run python scripts/check_docs_links.py` passes.
- Core tests and focused parser tests pass.
- A release tag or alpha package is available, or copy clearly says "install from source".

## Awesome MCP Servers Submission

Likely categories:

- Memory and Knowledge
- Developer Tools
- Monitoring or Observability
- Project Management, if the directory has that category

Suggested one-line entry:

```markdown
- [Geond Agent Protocol](https://github.com/geondongkim/geond-agent-protocol) - Local-first shared context, code/work evidence, reservations, handoffs, and dashboard visibility for heterogeneous AI agents via MCP and PostgreSQL.
```

Longer PR description:

```markdown
Geond is an alpha local-first MCP server and CLI for multi-agent shared context.
It imports Copilot Chat, Codex, and Claude Code sessions, stores redacted memory
in PostgreSQL/pgvector, indexes Python/TypeScript/JavaScript code graphs, links
changesets to symbols, supports file/symbol reservations and handoffs, and serves
a read-only dashboard. It can run fully local or point local agent processes at a
shared Azure/remote PostgreSQL database for team collaboration.
```

Submission notes:

- Avoid saying it is a complete enterprise platform.
- Avoid saying non-development SaaS adapters are implemented.
- Mention alpha and source install if no release package is available.
- Link to README, dashboard GIF, MCP config docs, and contribution guide.

## Manus Submission Workflow

Use Manus or another research/automation agent to prepare and track submissions,
but keep final review human-controlled.

1. Collect current README, docs links, release status, screenshots, and demo GIFs.
2. Draft the directory entry and PR body using the conservative copy above.
3. Compare target repository contribution rules and category naming.
4. Open a branch or issue draft in the target directory repository.
5. Have a human review for overclaiming, patent timing, and secret exposure.
6. Submit the PR or issue.
7. Track reviewer comments in a Geond handoff with next actions and risks.
8. Convert repeated questions into README or docs changes.

Manus prompt seed:

```text
Prepare an Awesome MCP Servers submission for Geond Agent Protocol. Use the
current README and docs, do not overclaim roadmap features, and classify it under
Memory/Knowledge and Developer Tools if available. Produce a one-line entry, PR
description, checklist of target repository rules, and follow-up tasks. Do not
include secrets, local patent drafts, or files from docs/patent, repo, tmp,
result, results, or videos.
```

## Outreach Plan

Phase 1: Credibility

- Pin README with short quickstart.
- Publish dashboard GIFs and a short demo script.
- Add GitHub topics.
- Create a first alpha release with checksums when ready.
- Share in MCP-focused communities with transparent alpha status.

Phase 2: Adoption

- Submit to Awesome MCP Servers and other MCP directories.
- Write a short post: "Local-first shared memory for heterogeneous AI agents".
- Publish a repository-agent walkthrough: Copilot import, Codex import,
  reservation, handoff, dashboard review.
- Document the Azure PostgreSQL shared DB validation as optional team mode.

Phase 3: Cross-Functional Expansion

- Design one non-development adapter proof of concept, such as QA test cases,
  product requirements, design tokens, or marketing campaign notes.
- Add examples showing a PM/QA/security/documentation agent consuming the same
  evidence as a coding agent.
- Use feedback to prioritize adapter APIs over broad UI changes.

## Metrics To Watch

- Setup success rate from fresh clone.
- Time to first MCP smoke success.
- Number of docs questions repeated in issues.
- Parser failures by source format.
- Dashboard browser smoke failures.
- Search relevance benchmark score.
- Handoff/reservation usage in real workflows.
- Security or privacy concerns reported by users.

## Risks In Public Promotion

- Patent timing: coordinate with filing strategy before disclosing deeper claim
  details in blog posts or demo scripts.
- Privacy: never use private transcripts in public demos.
- Setup complexity: make Docker/PostgreSQL tradeoffs explicit.
- Overclaiming: keep current implementation and roadmap visually separate.
- Enterprise expectations: be clear that IAM/RLS/audit streaming are not done.
