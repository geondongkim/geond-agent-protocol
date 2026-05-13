# Does geond actually help coding agents collaborate?

This note answers three questions that come up every time we hand the
protocol to a new pair of coding agents (currently GitHub Copilot Chat
and Codex, with Claude Code joining as a third tier-1 testbed):

1. Does geond enable *real* collaboration between coding agents, or is it
   just a shared notebook?
2. Is reading geond CLI/MCP output actually more efficient than reading
   the underlying git commits?
3. Does any of this help the human reviewing what an agent did?

The short answer is: geond is most useful for the *connective tissue*
between commits — intent, symbol-level impact, and the chat context
that produced the change. Git alone covers what changed; geond covers
why, who claimed it, and what to look at next.

## 1. Agent-to-agent collaboration

What geond gives two agents that git does not:

- **Reservations** (`reserve_files`, `reserve_symbols`, `get_active_reservations`,
  `get_symbol_conflicts`). Before an agent edits a file or a symbol it
  can advertise the claim. The next agent can either back off, pick a
  different slice, or pair on it deliberately. Without this both agents
  rebase onto each other and re-do work.
- **Reservation audit events** (`list_reservation_events`). Claim creation,
  renewal, release, and expiry are append-only events, so a later agent can
  distinguish an active conflict from an expired or deliberately released claim.
- **Conflict policy levels** (`get_workspace_coordination_policy`,
  `set_workspace_coordination_policy`). Workspaces can keep conflicts advisory,
  block them strictly, or require an explicit override reason before allowing a
  conflicting reservation.
- **Handoff summaries** (`record_handoff_summary`, `list_handoff_summaries`,
  `close_handoff_summary`). A structured, one-paragraph briefing with
  `next_steps`, `blocked_on`, tested commands, remaining risks, and a next
  action so the next agent starts oriented instead of re-reading the entire
  history.
- **Symbol-linked changesets** (`record_changeset`,
  `get_changeset_detail`). When agent A modifies `build_answer`, agent B
  can ask `get_symbol_context("build_answer")` and see which changeset
  touched it, what the patch was, and which other symbols moved with it.
  Git log alone surfaces the commit but not the symbol mapping.
- **Workspace lineage graphs** (`get_workspace_lineage_graph`,
  `geond://workspaces/{workspace_id}/lineage`). Agents can inspect a compact
  node/edge view of sessions, actions, handoffs, changesets, and benchmark
  runs without reconstructing provenance from separate tables.
- **Context review** (`review_workspace_context`, `geond review-context`). Before
  a new prompt-driven task starts, an agent can compare the requested intent,
  files, and symbols with active reservations, handoffs, and lineage matches,
  then decide whether to proceed, coordinate, or record more context. Use
  `--format markdown` for a compact preflight and JSON for automation.
- **Evidence-cited narratives** (`explain_change(include_narrative=True)`,
  `get_changeset_detail(include_narrative=True)`). Every claim in the
  narrative carries a `geond.evidence.v1` pointer like
  `changeset:abcdef12` so the consuming agent can verify the underlying
  row instead of trusting prose.

What geond does *not* solve:

- It does not pick a human winner when two agents both claim the same file. It
  can block or require an override reason, but the workflow still decides who
  should proceed.
- It does not run the agents. It is the substrate; the orchestrator
  (Copilot, Codex, Claude Code, or a custom runner) still drives.
- It does not enforce that an agent records its intent. An agent that
  edits files without calling `record_changeset` leaves the protocol
  blind, the same way a developer who never commits leaves git blind.

**Verdict.** geond meaningfully reduces the cost of two agents working
on the same codebase: claim, narrate, and look up each other's work
without scraping each other's chat logs. It is necessary connective
tissue, not a complete coordinator.

## 2. geond CLI/MCP vs reading git directly

| Question a reviewer asks | Git answer | geond answer |
| --- | --- | --- |
| "What files changed in commit abcdef12?" | `git show --stat` — fast. | `geond summarize-changeset abcdef12` — fast, plus symbol mapping. |
| "Why was this file changed?" | Commit message, if it is good. | `geond explain-change path/file.py --narrative` — pulls all changesets, related chat snippets (privacy-permitting), and snapshots. |
| "What other symbols moved when `build_answer` was modified?" | `git log -S build_answer` — slow, false positives. | `geond symbol-context build_answer` — exact, with evidence refs. |
| "Was there a chat conversation that produced this change?" | Not represented in git. | Returned as `related_messages` under `geond.evidence.v1`. |
| "Is anyone else editing this file right now?" | Not represented in git. | `get_active_reservations` / `get_symbol_conflicts`, plus `list_reservation_events` for recent lease history. |

Where git wins:

- For *what literally changed at the byte level*, `git diff` is the
  source of truth. geond stores patches but does not try to replace
  the diff viewer.
- For *long-term historical archaeology*, `git log` plus `git blame`
  remain the right tools. geond is most powerful for recent work where
  the chat, reservations, and intent are still available.

Where geond wins:

- Recovering the intent and the chat context behind a change.
- Following a change by symbol rather than by line range.
- Spotting active or recent conflicts before they become merge conflicts.
- Producing a deterministic, cite-able narrative an agent can quote
  rather than re-summarize.

**Verdict.** geond is more efficient when the question is "why did this
happen and what is touching it now"; git is more efficient when the
question is "what bytes changed and when". The two are complementary.

## 3. Does this help a human reviewer?

The narrative + evidence design was chosen precisely so that a human
reviewer can:

- Read a one-paragraph summary first (`narrative.headline` and
  `narrative.lines`).
- See a list of pointers like `[changeset:abcdef12, code_entity:1f3a...]`
  inline with each sentence.
- Pull the underlying evidence (`narrative.citations`) when a sentence
  looks suspicious, without leaving the MCP transcript.
- Switch to git for the raw diff when that is what they actually need.

Importantly, the narrative is **deterministic and non-LLM**: the same
inputs produce the same output. A reviewer is not auditing prose
generated by another model; they are auditing a structured template
filled with rows from the database.

Privacy modes (`strict`, `local-only`, `redacted-cloud`) decide whether
chat snippets are included in the narrative. A reviewer can rely on the
mode they configured — there is no hidden path for snippets to leak.

**Verdict.** For "what did the agents implement?" review, geond is a
better starting point than a commit list: it groups changes by intent,
links them to symbols, and cites them with pointers the reviewer can
expand. For "is this diff correct?", the reviewer still opens the diff.
The two flows reinforce each other.

## Open follow-ups

- **First-class editor reference workflows** for VS Code and Continue. Geond now
  has a storage/API boundary, a VS Code/LSP `Location[]` fixture normalizer, a
  generic stdio `collect-lsp-references` CLI with `auto`, `pyright`, and
  `typescript` profiles, plus VS Code task and Continue pre-query examples.
  The `geond install` command now previews or writes VS Code MCP and LSP task
  files and renders Claude Desktop/Continue config shapes. Follow-up work should
  move the same flow into editor-extension commands.
- **Agent-side conventions** documented per testbed (Copilot Chat,
  Codex, Claude Code) so each agent records changesets and handoffs
  consistently. See [`docs/agent_testbeds.md`](agent_testbeds.md).
