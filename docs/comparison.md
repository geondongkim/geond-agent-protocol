# Geond Compared With Adjacent Tools

Geond is easiest to understand by comparing it with tools developers already
know: agent memory systems, generic RAG MCP servers, git workflows, and agent
orchestrators.

Geond does not try to replace those tools. It fills the gap between them: shared
repo memory, reservations, handoffs, and review evidence for multiple AI agent
tools working on the same project.

## Short Version

| Tool category | Best at | Geond's difference |
| --- | --- | --- |
| Conversation memory systems | Remembering user facts, preferences, and chat history. | Geond stores repo-centered work evidence: files, symbols, changesets, reservations, handoffs, sessions, and dashboard read models. |
| Generic RAG MCP servers | Searching documents, folders, or knowledge bases. | Geond adds coordination state and evidence refs so agents can act on shared work context, not only retrieve text chunks. |
| Git and git worktree | Source-of-truth diffs, branches, history, and isolated workspaces. | Geond records why work happened, what is reserved now, which agent/session produced evidence, and what the next agent should do. |
| Agent orchestrators | Running, sequencing, or delegating work to agents. | Geond does not run agents. It gives Codex, Claude Code, Copilot, Antigravity, Manus, and custom MCP agents a shared evidence layer. |
| Project management tools | Human-visible task status and issue tracking. | Geond captures machine-readable agent evidence and exposes it through MCP, CLI, and a dashboard for review. |

## Detailed Comparison

| Capability | Memory systems | Generic RAG MCP | Git/worktree | Agent orchestrators | Geond |
| --- | --- | --- | --- | --- | --- |
| Durable chat/session import | Strong | Varies | Not represented | Varies | Implemented for several agent surfaces. |
| Code graph evidence | Usually absent | Usually absent | Indirect through source | Varies | Implemented for Python, TypeScript, and JavaScript indexing paths. |
| File/symbol reservations | Absent | Absent | Branch isolation only | Varies | Implemented with TTLs, conflict policies, and audit events. |
| Structured handoffs | Absent or generic | Absent | Commit messages only | Varies | Implemented as first-class handoff summaries. |
| Reviewer dashboard | Usually absent | Usually absent | External tools needed | Varies | Implemented as read-only dashboard views. |
| Shared team memory | Cloud service or custom DB | Varies | Remote repository only | Varies | Local-first, with optional shared PostgreSQL-compatible profiles. |
| LLM context control | Varies | Often chunk-heavy | Not applicable | Varies | Compact evidence refs, snippets, limits, and follow-up detail paths. |
| Runs agents | No | No | No | Yes | No. Geond is the coordination substrate. |

## Where Git Still Wins

Git remains the source of truth for raw diffs, commit history, branches, merges,
and blame. Geond should not be used as a replacement for reviewing code changes.

Use git when the question is:

- What bytes changed?
- Which commit introduced this line?
- How do I merge or revert a branch?
- What is the authoritative repository history?

Use Geond when the question is:

- Why did this agent change this file?
- Is another agent currently working on this file or symbol?
- What handoff did the previous agent leave?
- Which sessions, changesets, and evidence refs explain this work?
- Can another PC or local agent process see the same project memory?

## Where Memory Systems Still Win

Dedicated memory systems are better when the product needs user preference
memory, long-term personalization, or conversation recall outside a repository
workflow. Geond is strongest when the memory object is work evidence around a
project: files, symbols, commands, changesets, reservations, handoffs, sessions,
and reviewer state.

## Where Orchestrators Still Win

Agent orchestrators are better when the product needs to decide which agent runs
next, manage agent roles, or execute multi-step plans automatically.

Geond intentionally does not own that layer. It can support orchestrators by
giving them shared state, but public copy should avoid implying that Geond runs
or schedules agents by itself.

## Practical Positioning

Use this wording in public channels:

```text
Geond is a local-first MCP server for multi-agent repo coordination: shared
memory, code evidence, file/symbol reservations, handoffs, and dashboard review
backed by PostgreSQL.
```

Avoid this wording:

```text
Geond is an autonomous agent orchestrator that guarantees conflict-free coding.
```

That overclaims two things: Geond does not run the agents, and reservations help
prevent and surface conflicts rather than proving all future edits are conflict
free.
