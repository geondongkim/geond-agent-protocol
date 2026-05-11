# Agent Testbeds

This document summarizes the current validation surface for Geond across three
agent sources: GitHub Copilot Chat in VS Code, Codex, and Claude Code.

## Evaluation Summary

Claude's response to the Copilot-created prompt performed the testbed role well
as an investigation artifact:

- It identified the important Claude Code storage boundary: global
  `~/.claude/projects/{encoded-cwd}/{session}.jsonl` session files rather than
  project-local `.claude` chat logs.
- It correctly treated JSONL `cwd` as authoritative and the encoded project
  directory as best-effort only.
- It surfaced useful protocol signals: `sessionId`, `uuid`, `parentUuid`, `cwd`,
  `gitBranch`, `version`, `timestamp`, `thinking`, `tool_use`, and text blocks.
- It created a parser, sanitized fixture, and parser tests, which made Claude
  Code a useful third ingestion source.

The missing part was integration. A testbed is only convincing for Geond once it
can move from raw local storage to the shared protocol tables, redaction,
retrieval, and MCP-facing resources. That gap is now closed for Claude Code by
adding DB storage and CLI import paths.

## Testbed Matrix

| Source | Local Storage | Current Coverage | Strengths | Remaining Risks |
| --- | --- | --- | --- | --- |
| GitHub Copilot Chat in VS Code | `workspaceStorage/<hash>`, `state.vscdb`, `chatSessions`, `chatEditingSessions`, `GitHub.copilot-chat/transcripts` | Parser tests, fixture tests, DB import path, redaction, retrieval | Rich editor context and file snapshots | VS Code internal schema can change; live verification should stay opt-in because storage may contain private chat content |
| Codex | `~/.codex/session_index.jsonl`, `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` | Parser tests, DB import, redaction, retrieval, source filtering | Good second source for agent/tool event normalization; easy local JSONL validation | Large tool logs and reasoning-adjacent payloads need conservative retention defaults |
| Claude Code | `~/.claude/projects/{encoded-cwd}/{session}.jsonl` | Parser tests, DB import, redaction, retrieval, source filtering | Strong workspace metadata, parent/child event links, tool calls, cwd/git branch/version | `thinking` blocks should remain event-only and redacted; encoded directory names are not reliable workspace identities |

## Verification Status

- Copilot Chat: verified with sanitized fixture tests and existing parser
  integration. Live Copilot storage should be rechecked only with explicit
  operator consent.
- Codex: verified with sanitized JSONL fixture, DB import, redaction, and search.
- Claude Code: verified with sanitized JSONL fixture, parser tests, DB import,
  redaction, and search.

## Improvements Derived From The Testbeds

1. Treat every importer as a provenance-preserving adapter, not just a parser.
   Required bar: parse summary, raw event persistence, message extraction,
   redaction findings, repeat import behavior, and source-filtered retrieval.
2. Keep thinking/tool-only records out of user-facing message retrieval unless a
   privacy mode explicitly permits them. They are valuable as events, but risky
   as snippets.
3. Prefer `cwd` or explicit CLI workspace args over encoded storage paths.
4. Add importer-specific fixture cases whenever a new record type appears.
5. Use benchmark judgments built from fixtures so provider and gateway changes
   can be compared by quality, not only latency.
6. Use `geond://workspaces/{id}/timeline`, reservations, handoffs, and symbol
   resources as the common MCP validation path across all agents.

## Next Validation Scenarios

- Import one sanitized session from each source into the same workspace, then
  verify cross-source retrieval with `--source` filters.
- Run `index-tree-sitter` before symbol reservations so symbol conflicts resolve
  against syntax-derived qualified names.
- Save benchmark runs for OpenAI, Azure OpenAI, APIM gateway, and local
  OpenAI-compatible providers using the same judgments file.
- Record one handoff summary from each agent persona and verify timeline order.
- Exercise purge workflow after importing a fixture containing a fake secret.
