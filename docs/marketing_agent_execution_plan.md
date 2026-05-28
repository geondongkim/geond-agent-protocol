# Marketing Agent Execution Plan

Date: 2026-05-29 KST

This plan divides launch work between Codex and Antigravity while keeping the
repository, public claims, and git history under Codex review.

## Current Tool Evidence

| Tool | Evidence | Decision |
| --- | --- | --- |
| Codex | Running in the Geond worktree with shell, git, docs checks, and GitHub CLI access. | Primary owner for source-of-truth repository edits, validation, commits, and pushes. |
| Antigravity `agy` | `agy.exe` version `1.0.3` is installed at `%LOCALAPPDATA%\agy\bin\agy.exe`. `agy --print` can authenticate and generate model output, but stdout is unreliable and transcript logs must be inspected. | Use only for bounded copy variants, messaging reviews, and external-reader drafts. |
| GitHub CLI | `gh` is authenticated as the repository owner and can edit repository metadata. | Codex can update topics and description after verification. |

Antigravity output for this pass was captured in an ignored local transcript
under `.gemini/antigravity-cli/.../transcript.jsonl`; the temporary launcher log
is under `tmp/marketing_agy/`. These local logs are not public launch artifacts.

## Division Of Labor

| Work area | Owner | Why |
| --- | --- | --- |
| Repository edits, docs, templates, commits, pushes | Codex | Requires current worktree inspection, link checks, git hygiene, and claim-boundary enforcement. |
| GitHub metadata updates | Codex | Requires authenticated `gh` operations and verification against current repo state. |
| Public-claim review | Codex | Must stay aligned with implemented features and open-source readiness boundaries. |
| Launch copy variants | Antigravity via `agy`, then Codex review | `agy` is useful for fast wording alternatives, but Codex must remove overclaims and repo-inaccurate statements. |
| Community-specific post drafts | Antigravity via `agy`, then Codex review | Good fit for voice and angle exploration, but final posts need human approval. |
| Blog outline variants | Antigravity via `agy`, then Codex fact-check | Good for ideation; Codex keeps links, commands, and feature claims accurate. |
| Live community monitoring | Antigravity only if run through `agy` prompts or a confirmed tool path | Do not treat `agy` output as live web evidence unless the tool run explicitly proves browsing or source access. |
| Patent-sensitive wording | Human plus Codex guardrails | Avoid publishing patent details or unsupported legal claims from any agent. |

## Antigravity Tasks Allowed Through `agy`

Use `agy` for prompt-only tasks that do not require changing repository files:

1. Generate Show HN title variants in plain, low-hype style.
2. Generate community-specific angles for GeekNews, `r/mcp`, `r/LocalLLaMA`,
   `r/ClaudeAI`, and general AI coding audiences.
3. Draft outlines for technical blog posts.
4. Review whether marketing copy is understandable in 10 seconds.
5. List potential overclaim risks in proposed copy.
6. Draft localized post openings for the README languages, as long as Codex or a
   fluent reviewer checks them before publication.

Do not use `agy` for:

- committing, pushing, or editing source-of-truth files
- declaring live web research unless source access is proven
- changing GitHub repository metadata
- making patent or legal claims
- claiming Geond is an autonomous agent runner
- claiming Geond guarantees all conflicts are impossible

## Codex Review Of This `agy` Pass

Useful outputs:

- "Show HN: Geond - Shared memory and handoffs for multiple AI agents in one repo"
- "Show HN: Geond - Code graph evidence and shared state for AI pair coding"
- `r/mcp` angle around durable shared MCP state
- blog angles around statelessness, handoffs, reservations, and PostgreSQL

Rejected or adjusted outputs:

- "Git-like file and symbol reservations" is catchy but can confuse users
  because git remains the source of truth and Geond does not implement git-like
  branching.
- "Coordinate Codex and Antigravity" is too narrow for the headline. Keep Codex
  plus Antigravity as a verified example, not the product category.
- "file-based memory" is inaccurate. Geond's durable memory uses PostgreSQL and
  pgvector.
- "lock paths" should be softened to "reserve files or symbols" because Geond
  supports policy-driven reservations, not a universal filesystem lock.
- Antigravity's recommendation that `agy` own structural roadmap and file
  reservations is too broad for the current CLI behavior. Keep `agy` on copy and
  review drafts unless a later run proves reliable edit/verification behavior.

## Executed By Codex In This Pass

- Verified clean `main` worktree before changes.
- Ran `agy --print` with a bounded launch-copy prompt and inspected the local
  transcript.
- Updated GitHub repository description.
- Added repository topics for MCP, local-first, multi-agent, PostgreSQL, and
  AI coding discovery.
- Added a comparison document for adjacent tool categories.
- Added issue templates and a PR template with alpha, privacy, and validation
  checks.

## Next Antigravity Prompt

```text
You are Antigravity via agy. Use only the context in this prompt. Draft localized
community post openings for Geond Agent Protocol in English, Korean, Japanese,
Simplified Chinese, Spanish, French, and German. Target channels: Hacker News,
GeekNews, Zenn/Qiita, V2EX, HackniA/Menéame, LinuxFr, and German developer
communities. Product boundary: Geond is a local-first MCP server and CLI for
shared repo memory, file/symbol reservations, handoffs, code graph evidence,
compact evidence refs, dashboard review, and optional shared PostgreSQL team
mode. It does not run agents and does not guarantee automatic merge-conflict
resolution. Keep Codex + Antigravity as one verified example, not the category.
Return only draft copy, risk notes, and which audience each draft targets. Do
not ask to edit files.
```

## Next Codex Tasks

1. Generate or update a short "reservation prevents collision" GIF.
2. Add GitHub social preview art.
3. Draft Awesome MCP Servers PR body from `docs/marketing_strategy.md`.
4. Draft Show HN first comment and human review checklist.
5. Draft and review GeekNews plus localized launch posts from
   `docs/international_launch_channels.md`.
6. Keep `docs/open_source_readiness.md` synced with completed launch hygiene.
