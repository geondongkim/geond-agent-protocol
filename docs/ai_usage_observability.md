# AI Usage Observability And Tokenmaxxing

## Purpose

This document describes how Geond should collect AI usage, prompt counts, token estimates, cost estimates, and evidence-linked productivity signals without encouraging unhealthy tokenmaxxing behavior.

Tokenmaxxing means optimizing for high AI token consumption as a status or performance signal. PMs may ask for token counts, prompt counts, model usage, and AI adoption dashboards. Geond can support those questions, but it should connect usage to evidence rather than reward raw consumption.

## Current Capabilities

| Signal | Current status | Source |
| --- | --- | --- |
| Session count | Available | `sessions` |
| Message count | Available | `messages` |
| User prompt count | Available | message role counts |
| Assistant reply count | Available | message role counts |
| Captured prompt count | Available | `metadata_or_text` and dashboard role counts |
| Technical/tool trace count | Available | technical message roles and events |
| Agent action count | Available | `agent_actions` |
| Reservation count | Available | file and symbol reservations |
| Handoff count | Available | `handoff_summaries` |
| Changeset count | Available | `changesets` |
| Benchmark run count | Available | `benchmark_runs` |
| Model/provider metadata | Partially available | session metadata and benchmark metadata |
| Exact input/output token count | Schema and summary storage started | `llm_usage_events` |
| Estimated cost | Pricing registry storage started | `model_pricing`, `llm_usage_events.estimated_cost_usd` |
| Tokenmaxxing detection | Not implemented yet | needs usage versus evidence scoring |

## Why Raw Token Counts Are Dangerous

A token leaderboard creates perverse incentives:

- people may burn tokens to look AI-native
- agents may run unnecessary loops
- teams may inflate compute cost without better output
- junior staff may hide confusion by prompting more
- high usage may be misread as high productivity
- low usage may be misread as low adoption even when work quality is high

Geond should avoid a default "top token users" dashboard. The default should be "usage versus evidence."

## Usage Versus Evidence

Good PM questions:

- Did AI usage lead to a changeset?
- Did the changeset include tests?
- Did the agent leave a handoff?
- Did another agent consume the handoff?
- Was expensive model usage reserved for high-risk work?
- Did high usage reveal a training or mentoring need?
- Which workflows create repeatable value?

Bad PM questions:

- Who used the most tokens?
- Who prompted the most?
- Who created the most sessions?
- Which individual is lowest on the AI leaderboard?

## Proposed Data Model

Implementation started in `schemas/003_llm_usage.sql`. The schema includes
`source_record_id`, `priced_at`, workspace/session/agent/model indexes, and a
partial unique index on `(source, source_record_id)` for idempotent importer
replays.

```sql
CREATE TABLE llm_usage_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid REFERENCES workspaces(id) ON DELETE CASCADE,
    session_id uuid REFERENCES sessions(id) ON DELETE SET NULL,
    agent_id uuid REFERENCES agents(id) ON DELETE SET NULL,
    source text NOT NULL,
    provider text,
    model text,
    operation text,
    input_tokens integer,
    output_tokens integer,
    cached_input_tokens integer,
    reasoning_tokens integer,
    total_tokens integer,
    estimated boolean NOT NULL DEFAULT false,
    estimated_cost_usd numeric,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
```

Recommended indexes:

```sql
CREATE INDEX idx_llm_usage_workspace_created
    ON llm_usage_events(workspace_id, created_at DESC);

CREATE INDEX idx_llm_usage_session
    ON llm_usage_events(session_id);

CREATE INDEX idx_llm_usage_agent
    ON llm_usage_events(agent_id, created_at DESC);

CREATE INDEX idx_llm_usage_model
    ON llm_usage_events(provider, model);
```

## Token Accuracy Levels

| Level | Meaning | `estimated` |
| --- | --- | --- |
| Provider-reported | Provider or tool metadata gave exact usage. | `false` |
| Adapter-estimated | Adapter estimated tokens from stored prompt and response text. | `true` |
| Session-estimated | Only session/message counts are available; token count is approximate. | `true` |
| Unknown | No reliable token data. | `true`, token fields null |

## Importer Responsibilities

Codex importer:

- reads model and provider metadata when present
- detects usage-like fields in raw JSONL records
- writes `llm_usage_events` when token data exists
- falls back to estimated tokens from user/assistant messages
- uses stable `source_record_id` values for idempotent reimports

Claude Code importer:

- preserves model and tool call metadata
- extracts usage from message metadata if present
- treats thinking blocks carefully; private chain-of-thought is not stored as usage evidence
- stores text-message estimates without raw hidden reasoning text
- uses stable `source_record_id` values for idempotent reimports

VS Code Copilot importer:

- extract prompt and response events from chat sessions and transcripts
- preserve source session IDs
- use best-effort estimates if provider usage is unavailable

All importers:

- must redact sensitive metadata before persistence
- must not fail import when usage extraction fails
- must mark estimated usage clearly
- should attach source record IDs for auditability

## Model Pricing

Implementation started in `schemas/004_model_pricing.sql` and
`src/geond/storage/pricing.py`. `insert_usage_event` now snapshots
`estimated_cost_usd` and `priced_at` when a provider/model price is available,
so later price changes do not silently rewrite historical usage reports.

Add a pricing registry rather than hardcoding costs in queries.

```sql
CREATE TABLE model_pricing (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    provider text NOT NULL,
    model text NOT NULL,
    input_usd_per_1m_tokens numeric,
    output_usd_per_1m_tokens numeric,
    cached_input_usd_per_1m_tokens numeric,
    reasoning_usd_per_1m_tokens numeric,
    effective_from timestamptz NOT NULL DEFAULT now(),
    effective_to timestamptz,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);
```

Pricing changes over time. Cost reports should store the price version or compute estimates with a clear `priced_at` timestamp.

## Dashboard Views

| Panel | Purpose |
| --- | --- |
| Usage Summary | total sessions, prompts, tokens, estimated cost, and estimate ratio |
| Usage by Source | Codex, Claude Code, VS Code Copilot, Continue, MCP client |
| Usage by Model | provider/model token and cost rollups |
| Usage by Evidence | tokens compared with changesets, tests, handoffs, reservations, and benchmark runs |
| Risk Signals | high usage with weak output evidence |
| Enablement Signals | high prompting with repeated unresolved handoffs or no tests |
| Data Quality | exact versus estimated token share |

## Anti-Tokenmaxxing Signals

These are not disciplinary metrics. They are review signals.

| Signal | Meaning |
| --- | --- |
| `high_usage_low_changeset` | High token or prompt count with few or no changesets. |
| `high_prompts_no_handoff` | Many prompts but no durable handoff or next action. |
| `expensive_model_low_risk_task` | High-cost model used repeatedly for low-complexity work. |
| `repeated_sessions_same_intent` | Many sessions appear to repeat the same unresolved task. |
| `many_tool_traces_no_tests` | Many tool events but no tested command evidence. |
| `stale_reservation_high_activity` | Claimed work remains active while unrelated activity continues. |
| `low_usage_high_output` | Useful pattern: low usage with high-quality output. Should not be penalized. |
| `high_usage_training_signal` | High usage may indicate the person or agent needs examples, mentoring, or better prompts. |

## Privacy And Governance Rules

- Use team and workspace rollups by default.
- Do not rank individuals by raw token count on the first dashboard screen.
- Show exact versus estimated usage clearly.
- Use redacted content for usage accounting.
- Keep raw prompts behind existing privacy modes.
- Let teams disable personal drilldown.
- Document that token usage alone is not a performance metric.

## Implementation Order

1. Add `llm_usage_events` schema and tests.
2. Add usage extraction in importers where metadata is available.
3. Add tokenizer-based fallback estimates.
4. Add model pricing registry.
5. Add CLI reports:
   - `usage-summary`
   - `usage-by-agent`
   - `usage-by-model`
   - `usage-risk-signals`
6. Add dashboard Usage view.
7. Add privacy controls for personal drilldown.
8. Add export for PM reports.

## Agent Guidance

When implementing this feature, do not build a token leaderboard first. Build evidence-linked usage first.

A good first report says:

```text
Workspace used about 1.2M AI tokens this week.
72% of usage is exact provider-reported data.
28% is estimated from imported messages.
Usage produced 14 changesets, 9 tested-command handoffs, and 3 open risks.
Two high-usage sessions have no changeset or handoff evidence and should be reviewed.
```

A bad first report says:

```text
Alice is rank 1 with 800K tokens.
Bob is rank 12 with 20K tokens.
```

