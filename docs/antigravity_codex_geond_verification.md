# Antigravity 2.0, Codex CLI, and Geond Verification

Date: 2026-05-29 KST
Workspace: `C:\Users\EL035\dataschool\geond-agent-protocol`

This note records a local verification pass for Antigravity 2.0, Codex CLI
0.125.0, Codex's `mcp-server` command, and Geond as the shared context layer.

## Summary

The Antigravity-generated report is mostly right about the Codex CLI side:
`codex-cli 0.125.0` is installed, `codex exec` works non-interactively,
the configured model is `gpt-5.5`, the provider is OpenAI, approval is `never`,
and the observed sandbox is `workspace-write`.

One additional nuance appeared when testing MCP: the PATH CLI reports
`codex-cli 0.125.0`, while the Codex Desktop embedded executable used for a
direct MCP JSON-RPC smoke reported `codex-mcp-server` version `0.133.0`. Treat
CLI version and Desktop MCP runtime version as separate surfaces until the
installer makes that relationship clearer.

The Antigravity CLI side is now verifiable after installing the standalone
`agy` binary. The first suggested root command, `irm https://antigravity.google
| iex`, is not a valid installer target on this machine because the root URL
returns HTML. The working official installer endpoint is
`https://antigravity.google/cli/install.ps1`, documented from
<https://antigravity.google/docs/cli-install>. It installed `agy.exe` to
`C:\Users\EL035\AppData\Local\agy\bin\agy.exe`, not to
`C:\Users\EL035\AppData\Roaming\Antigravity\bin`.

`agy --print` successfully authenticated through the local Antigravity keyring,
selected `Gemini 3.5 Flash (Medium)`, called the Google backend, and wrote the
exact model response to its transcript log. One important CLI quirk remains:
stdout/stderr capture was empty even when the transcript contained the correct
response. A follow-up test using `Start-Process -RedirectStandardOutput` with a
properly quoted argument string produced the same result: exit `0`, transcript
response present, stdout/stderr files still `0` bytes.

Geond is now wired into Antigravity's MCP config through
`C:\Users\EL035\.gemini\config\mcp_config.json`, which is also the target of the
Antigravity state symlink at `C:\Users\EL035\.gemini\antigravity\mcp_config.json`.

## Commands Verified

```powershell
codex --version
codex --help
codex exec --help
codex apply --help
codex mcp-server --help
echo "" | codex exec "Hello! Please reply with exactly 'Codex test success'." --ephemeral --output-last-message codex_test_output.txt
echo "" | codex exec "Please reply exactly: CODEX_SIDE_BY_SIDE_20260529" --ephemeral --output-last-message codex_side_by_side_20260529_001810.final.txt
# Direct stdio JSON-RPC smoke against Codex Desktop's embedded codex.exe mcp-server:
# initialize -> tools/list

irm https://antigravity.google/cli/install.ps1 | iex
C:\Users\EL035\AppData\Local\agy\bin\agy.exe --version
C:\Users\EL035\AppData\Local\agy\bin\agy.exe --help
C:\Users\EL035\AppData\Local\agy\bin\agy.exe --print "Please reply exactly: AGY_SIDE_BY_SIDE_20260529" --print-timeout 2m --log-file agy_side_by_side_20260529_001730.log

uv run geond doctor --skip-mcp
uv run geond migrate
uv run geond seed-sample
uv run geond mcp-smoke --format json --query app_context --workspace-uri file:///sample/geond --limit 3 --allow-empty-search
uv run geond benchmark-search app_context service.py --mode keyword --repeat 5 --workspace-uri file:///sample/geond --save --label antigravity-codex-compare-keyword --format json
uv run geond benchmark-search app_context service.py --mode keyword --repeat 5 --workspace-uri file:///sample/geond --rerank local --candidate-limit 30 --save --label antigravity-codex-compare-keyword-rerank --format json
```

## Codex CLI Findings

| Claim | Local result | Verdict |
| --- | --- | --- |
| Codex CLI version is `0.125.0` | `codex-cli 0.125.0` | Confirmed |
| `codex exec` can run non-interactively | Exact prompt returned `Codex test success` | Confirmed |
| Model is `gpt-5.5` | `~/.codex/config.toml` and exec banner both show `gpt-5.5` | Confirmed |
| Provider is OpenAI | Exec banner shows `provider: openai` | Confirmed |
| Approval is `never` | Exec banner shows `approval: never` | Confirmed |
| Sandbox is `workspace-write` | Exec banner shows `sandbox: workspace-write` | Confirmed |
| `codex apply` exists | `codex apply --help` says it applies latest Codex diff via `git apply` | Confirmed |
| `codex mcp-server` exists | `codex mcp-server --help` says it starts Codex as a stdio MCP server | Confirmed |
| Codex MCP protocol responds | Direct JSON-RPC smoke returned server `codex-mcp-server` and tools `codex`, `codex-reply` | Confirmed |

One nuance: the sandbox writable root is tied to the current `workdir`. In this
verification run it was `C:\Users\EL035\Documents\Antigravity 2`, not ONMU.
The earlier report's ONMU path was therefore task-context-specific, not a
global Codex property.

MCP nuance: this protocol smoke used Codex Desktop's embedded
`codex.exe`, which reported server version `0.133.0`. The npm PATH CLI still
reported `codex-cli 0.125.0`.

## Antigravity Findings

| Claim or observation | Local result | Verdict |
| --- | --- | --- |
| Antigravity 2.0 is installed | `Antigravity.exe` file version `2.0.6`; running processes found | Confirmed |
| Standalone Antigravity CLI is installed | `agy.exe` version `1.0.3` at `C:\Users\EL035\AppData\Local\agy\bin\agy.exe` | Confirmed |
| Root URL is the CLI installer | `https://antigravity.google` returned HTML; the valid script was `/cli/install.ps1` | Corrected |
| Installer target is `%APPDATA%\Antigravity\bin` | Official installer used `%LOCALAPPDATA%\agy\bin`; `%APPDATA%\Antigravity\bin` still only had `agy-node.cmd` | Corrected |
| Antigravity uses Google backend services | Logs show `generativelanguage.googleapis.com` and `daily-cloudcode-pa.googleapis.com` | Confirmed |
| Antigravity has planner / task behavior | Older extension logs show `planner_generator` requests and terminal command completion records | Supported |
| Antigravity CLI can be driven like `codex exec` | `agy --print` exits `0` and transcript contains the exact response | Confirmed with output-channel caveat |
| Antigravity CLI prints response to stdout | Captured stdout/stderr file was `0` bytes, while transcript had the response | Not in this environment |
| `Start-Process -RedirectStandardOutput` fixes `agy --print` stdout | Properly quoted Start-Process run still wrote `0` bytes to stdout/stderr | Not confirmed |
| `google-antigravity-sdk` plugin is installed | `C:\Users\EL035\.gemini\config\plugins\google-antigravity-sdk\plugin.json` exists, version `0.0.4` | Confirmed as metadata only |
| Exact selected model is "Gemini 3.5 Medium" | CLI transcript metadata records `Gemini 3.5 Flash (Medium)` | Corrected / confirmed |

The Antigravity language server had been repeatedly failing to parse
`C:\Users\EL035\.gemini\config\mcp_config.json` because it was an empty JSON
file. That has been fixed by writing a valid `mcpServers.geond` entry.

The CLI-side Geond connection also materialized tool schemas under
`C:\Users\EL035\.gemini\antigravity-cli\mcp\geond`, including
`search_dev_memory`, `review_workspace_context`, `record_changeset`,
`record_agent_action`, `reserve_files`, and `get_dashboard_overview`.

## Antigravity Geond MCP Wiring

Active config:

```json
{
  "mcpServers": {
    "geond": {
      "command": "uv",
      "args": [
        "--directory",
        "C:/Users/EL035/dataschool/geond-agent-protocol",
        "run",
        "geond-mcp"
      ],
      "env": {
        "GEOND_DATABASE_PROFILE": "local",
        "GEOND_DATABASE_URL": "postgresql://geond:geond_dev_password@localhost:55432/geond",
        "GEOND_PRIVACY_MODE": "local-only",
        "GEOND_EMBEDDING_PROVIDER": "none"
      }
    }
  }
}
```

### macOS follow-up, 2026-06-07

On macOS, the active Antigravity MCP config is
`/Users/geondongkim/.gemini/config/mcp_config.json`, and
`/Users/geondongkim/.gemini/antigravity/mcp_config.json` is a symlink to that
file. `uv run geond install --client antigravity --write` writes a
`mcpServers.geond` entry with:

- `command`: `uv`
- `args`: `--directory`, `/Users/geondongkim/geond-agent-protocol`, `run`,
  `geond-mcp`
- `env`: `GEOND_DATABASE_PROFILE=local`, `GEOND_PRIVACY_MODE=local-only`, and
  `GEOND_EMBEDDING_PROVIDER=none`

`uv run geond doctor --format json` reports `antigravity_config`,
`antigravity_config_link`, and `antigravity_cli` as ok. The overall doctor
status can still report unrelated local setup errors such as missing Docker.
With the same local-only/no-embedding environment, `uv run geond mcp-smoke
--format json --allow-empty-search` initializes `geond-agent-protocol`, lists
55 tools, reads 2 resources and 14 resource templates, and can call
`search_dev_memory` in keyword mode.

The standalone CLI exists at `/Users/geondongkim/.local/bin/agy` and supports
`--print`, `--add-dir`, `--sandbox`, and `--print-timeout`. A documentation
worker smoke using `agy --sandbox --add-dir
/Users/geondongkim/geond-agent-protocol --print ...` could read repo-local docs,
but reported that MCP tools were not visible in that print session. Treat
`agy --print` as a documentation draft channel unless a future smoke proves MCP
tool calls from print mode; use Antigravity's interactive MCP client surface,
`geond doctor`, and `mcp-smoke` as the MCP connectivity evidence.

`local-only` and `GEOND_EMBEDDING_PROVIDER=none` were chosen so Antigravity can
use keyword/evidence search without silently triggering extra cloud embedding
calls. Vector or hybrid retrieval can be enabled later by switching the provider
to OpenAI, Azure OpenAI, an OpenAI-compatible local endpoint, or Ollama.

## Codex MCP Server vs Geond MCP

| Surface | What it is | Best use |
| --- | --- | --- |
| `codex exec` | Non-interactive Codex run from the terminal | One-shot implementation, review, shell-driven automation |
| `codex mcp-server` | Starts Codex itself as a stdio MCP server; protocol smoke exposed `codex` and `codex-reply` tools | Let another MCP client ask Codex to act as an agent |
| `geond-mcp` | Starts Geond's shared context MCP server | Shared memory, retrieval, evidence refs, reservations, handoffs, dashboard read models |

These are complementary, not substitutes. `codex mcp-server` exposes an agent.
`geond-mcp` exposes the shared memory and coordination substrate that agents can
use. The useful architecture is:

```text
Antigravity 2.0 -> Geond MCP -> shared evidence, reservations, handoffs
Codex CLI      -> Geond CLI/MCP -> imported sessions, changesets, benchmarks
Antigravity    -> Codex MCP server, optional -> delegate specific coding tasks
```

After `agy` installation, Antigravity can now participate through both paths:
as an MCP client of Geond and as a headless CLI run target. It should not yet be
treated as a drop-in replacement for `codex exec` in automation until stdout
behavior is fixed or the transcript importer becomes the official capture path.

## Measured Timings

| Measurement | Result |
| --- | ---: |
| `codex exec` exact-reply smoke wall time | 17,800 ms |
| `codex exec` token usage for that run | 24,088 tokens |
| `codex exec` side-by-side exact-reply wall time | 12,075 ms |
| `codex exec` side-by-side token usage | 24,089 tokens |
| `agy --print` side-by-side exact-reply wall time | 8,262 ms |
| `agy --print` side-by-side stdout/stderr capture | 0 bytes |
| `agy --print` side-by-side transcript response | `AGY_SIDE_BY_SIDE_20260529` |
| `agy --print` via quoted `Start-Process` wall time | 9,053 ms |
| `agy --print` via quoted `Start-Process` stdout/stderr capture | 0 bytes |
| `agy --print` via quoted `Start-Process` transcript response | `AGY_STARTPROCESS_QUOTED_20260529` |
| Geond `migrate` wall time | 1,451 ms |
| Geond `seed-sample` wall time | 1,578 ms |
| Geond MCP smoke wall time | 2,659 ms |
| Geond MCP smoke using the configured `uv --directory ... run geond-mcp` args | 2,279 ms |
| Geond saved keyword benchmark wall time | 1,394 ms |
| Geond saved keyword + local rerank benchmark wall time | 1,344 ms |

Saved Geond benchmark rows:

| Label | Mode | Queries | Results | Mean avg ms | Created |
| --- | --- | ---: | ---: | ---: | --- |
| `antigravity-codex-compare-keyword` | keyword | 2 | 3 | 4.584 | 2026-05-28T14:56:35.262100+00:00 |
| `antigravity-codex-compare-keyword-rerank` | keyword + local rerank | 2 | 3 | 4.418 | 2026-05-28T14:56:35.170216+00:00 |
| `readme-dashboard-demo` | keyword | 2 | 9 | 19.75 | 2026-05-28T14:56:13.323704+00:00 |

Warm quality-check run, not saved:

| Query | Result count | Min ms | Avg ms | Max ms | Quality |
| --- | ---: | ---: | ---: | ---: | --- |
| `app_context` | 1 | 1.642 | 2.770 | 4.906 | Recall@k 1.0, MRR 1.0, nDCG@k 1.0 |
| `service.py` | 2 | 1.431 | 2.022 | 3.618 | No judgment fixture for this query |

Antigravity CLI wall time is measurable from the launching shell, but token
usage was not visible in the inspected transcript/logs. The transcript contains
clean user/model step records and model selection metadata, so it is suitable
for a Geond importer even before Antigravity exposes structured stdout.

Transcript structure observed so far:

| Run type | Transcript fields | Notes |
| --- | --- | --- |
| Exact-reply smoke | `step_index`, `source`, `type`, `status`, `created_at`, `content` | Enough to recover user prompt and final model response |
| Tool-using exploratory run | plus `thinking`, `tool_calls`, `truncated_fields` | Enough to recover planner/tool activity and MCP/tool execution records |

No `input_tokens`, `output_tokens`, `total_tokens`, `usage_metadata`, or similar
token usage fields were found in the inspected Antigravity CLI `brain` and
`log` directories.

## What Was Learned

1. Codex CLI is easy to measure from the shell because it exposes a stable
   non-interactive command, stdout banner, final-message file, and token report.
2. Antigravity is now benchmarkable headlessly through `agy --print`, but its
   current stdout behavior makes transcript-log capture necessary for reliable
   automation on this machine.
3. OS-level redirection is not a dependable workaround here. `Start-Process`
   can capture some streamed planner text in misquoted or exploratory runs, but
   it did not capture the final exact-response output when arguments were quoted
   correctly.
4. Geond can already measure its own retrieval latency and MCP startup/smoke
   path, but it does not yet store generic agent run timing as a first-class
   benchmark type.
5. The strongest comparison should separate three timing classes:
   prompt-to-agent-response, MCP tool latency, and retrieval/storage latency.
6. Codex has at least two visible local runtime surfaces on this machine:
   the npm PATH CLI at `0.125.0` and the Desktop embedded MCP runtime reporting
   `0.133.0`.

## Geond Improvement Plan

### P0: Add Antigravity Client Support To `geond install`

Add `--client antigravity` that writes
`C:\Users\<user>\.gemini\config\mcp_config.json` with a `mcpServers.geond`
entry. It should detect that
`C:\Users\<user>\.gemini\antigravity\mcp_config.json` may be a symlink to the
active config path.

Acceptance checks:

- Preview and write modes work like existing VS Code and Claude Desktop flows.
- Existing non-Geond MCP servers are preserved.
- Empty or malformed JSON is repaired only after writing a backup.
- `geond doctor` reports whether Antigravity MCP config is valid.
- The check distinguishes desktop app shim files such as `agy-node.cmd` from
  the standalone CLI binary at `%LOCALAPPDATA%\agy\bin\agy.exe`.

### P0: Add Agent Run Benchmark Records

Extend benchmarks beyond retrieval:

- command: `codex exec`, future Antigravity CLI, `geond mcp-smoke`
- prompt hash and prompt label
- wall time
- model/provider when available
- sandbox and approval policy when available
- final output hash or short redacted excerpt
- token usage when available
- stdout/stderr paths or compact diagnostics
- transcript/log paths for agents such as `agy` that persist correct answers
  outside stdout

This would let Geond compare Codex, Antigravity, Claude Code, Copilot, and MCP
clients with actual stored timing evidence rather than manual notes.

### P1: Add Antigravity CLI Transcript Importer

Create an importer for:

- `C:\Users\<user>\AppData\Roaming\Antigravity\logs\language_server.log`
- `C:\Users\<user>\AppData\Roaming\Antigravity\logs\**\google.antigravity\Antigravity.log`
- `C:\Users\<user>\.gemini\antigravity\...` state and history files where safe
- `C:\Users\<user>\.gemini\antigravity-cli\brain\*\.system_generated\logs\transcript.jsonl`
- `C:\Users\<user>\.gemini\antigravity-cli\log\cli-*.log`

Map these to Geond:

- `sessions` for task or chat threads when identifiers are discoverable
- `agent_actions` for planner requests, shell commands, and task state changes
- `llm_usage_events` when token or model metadata is available
- `redaction_findings` for anything sensitive before persistence
- `agent_run_benchmarks` using shell wall time plus transcript response hashes
  when stdout is empty

Token usage import should be nullable. The importer should store model label and
step counts immediately, then attach token counts only if a future Antigravity
CLI build exposes usage fields in transcript or log data.

### P1: Add MCP Audit Events

Geond already has strong evidence objects, but it should record MCP call
telemetry separately:

- tool/resource name
- client name when available
- request/response byte size
- elapsed milliseconds
- status and error type
- redacted input/output preview
- evidence refs created or returned

This supports speed comparison, payload bloat detection, and debugging client
integration issues.

### P1: Compact-By-Default MCP Contract

Keep the MCP surface evidence-first:

- default responses should return ids, scores, short snippets, and evidence refs
- raw transcripts and large payloads should require explicit detail calls
- response-size tests should fail when common tools exceed a budget
- dashboard read models may be richer than MCP responses

This follows the existing Geond direction: use MCP as a shared evidence protocol,
not a raw transcript pipe.

### P1: Codex MCP Server Integration Guide

Document two supported paths:

1. Codex as an MCP client of Geond, reading/writing shared context.
2. Codex as an MCP server, callable from Antigravity or another orchestrator.

The guide should clarify that `codex mcp-server` does not replace Geond. It
exposes Codex as an agent; Geond remains the memory and coordination layer.

### P2: Improve Local Postgres Startup Diagnostics

This run hit a Docker Compose name conflict because an existing stopped
`geond-postgres` container already owned the name and port mapping. `geond
doctor` should detect this and suggest:

```powershell
docker start geond-postgres
```

instead of leaving the user to infer why `docker compose up -d postgres`
failed.

### P2: Add A Comparison Command

Add:

```powershell
uv run geond compare-agents --prompt-file prompts/smoke.txt --agent codex --agent antigravity --agent geond-mcp
```

For Codex, it can call `codex exec`. For Antigravity, it can use a future CLI,
SDK, or log-observed task launcher. For Geond, it can run MCP smoke and
retrieval benchmarks. Results should save into benchmark/agent-run tables and
render as Markdown.

## Next Implementation Slice

The highest-value next slice is small:

1. Add `antigravity` to `geond install`.
2. Add `geond doctor` checks for Antigravity MCP config validity.
3. Add a first `agent_run_benchmarks` table or reuse `benchmark_runs` with a
   `kind` field.
4. Add docs for `codex mcp-server` versus `geond-mcp`.

That would turn this manual verification into a repeatable local command.
