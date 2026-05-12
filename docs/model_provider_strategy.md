# Model and Provider Strategy

This document explains how to choose a text embedding provider for Geond. The
project now has three verified paths:

- OpenAI `text-embedding-3-small` as the MVP baseline.
- Azure OpenAI `text-embedding-3-small` as the managed Azure validation path.
- A local multilingual SLM embedding benchmark using
  `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` on a temporary
  Azure B2s VM.

Geond keeps keyword, vector, and hybrid retrieval modes side by side. Keyword
mode works without embeddings. Vector and hybrid modes require embeddings.
Hybrid mode is the default recommendation for real agent memory because it keeps
exact lexical matches while adding semantic recall across languages and wording
differences.

## Current Embedding Options

| Option | Provider mode | Verified model | Dimensions | Status |
| --- | --- | --- | ---: | --- |
| OpenAI baseline | `openai` | `text-embedding-3-small` | 1536 | MVP default and first successful live baseline. |
| Azure OpenAI | `azure-openai` | Azure deployment of `text-embedding-3-small` | 1536 | Validated with S0 `GlobalStandard` capacity `7`. |
| APIM gateway | `gateway` or `openai-compatible` | Routes to an OpenAI-compatible backend | Backend-dependent | Gateway scaffold validated; full APIM policy remains opt-in. |
| Local SLM | `local-openai-compatible` or `ollama` for serving; VM benchmark used Python directly | `paraphrase-multilingual-MiniLM-L12-v2` | 384 | Validated for Korean/English retrieval probes on B2s VM. |
| Disabled | `none` or `disabled` | None | N/A | Keyword retrieval only. |

The initial Postgres schema uses `embedding vector(1536)`, which matches
`text-embedding-3-small`. Local models with different dimensions need a schema
migration or a separate vector column strategy before production use.

## Comparison Matrix

| Criterion | OpenAI `text-embedding-3-small` | Azure OpenAI service | Local multilingual SLM |
| --- | --- | --- | --- |
| Main audience | Fast MVPs, OSS demos, provider-neutral development | Teams already on Azure, regulated environments, enterprise identity | Local-first users, offline work, private codebases, cost-sensitive experiments |
| Data use and training posture | Text leaves the machine and is sent to OpenAI API; use redaction before calls | Text leaves the machine and enters the configured Azure region/resource boundary; supports enterprise controls and Entra ID | Text stays on the machine or private VM when served locally; strongest default for sensitive memory |
| Security controls | API key, organization/project controls, local `.env` hygiene | API key or Entra ID, RBAC, managed identity, APIM, Key Vault, private networking options | OS/process isolation, local network controls, no cloud model API key required |
| Model updates | Managed by OpenAI model lifecycle | Managed through Azure model catalog and deployment versions | Controlled by pinned package/model artifact; user must update intentionally |
| Speed | Usually fast for batch embedding, network-dependent | Similar model quality, region and quota dependent; capacity matters | CPU on small VM is slower to load but predictable after warm-up; GPU can improve throughput |
| Ecosystem | Broad SDK/docs support and strong baseline compatibility | Azure governance, APIM, Monitor, Cost Management, Foundry integration path | Hugging Face, sentence-transformers, Ollama, llama.cpp, TEI ecosystem |
| Reliability risks | External API availability, rate limits, key management | Azure quota/capacity, deployment naming, APIM policy complexity | Model download time, local runtime setup, dimension mismatch, CPU/RAM constraints |
| Best retrieval mode | Hybrid | Hybrid | Hybrid after local vector serving is configured; keyword fallback remains useful |

## Environment Setup Guide

### OpenAI MVP Baseline

Use this when you want the shortest path to vector and hybrid retrieval.

```env
GEOND_PRIVACY_MODE=redacted-cloud
GEOND_EMBEDDING_PROVIDER=openai
GEOND_EMBEDDING_MODEL=text-embedding-3-small
GEOND_EMBEDDING_API_KEY=<openai-api-key>
GEOND_EMBEDDING_DIMENSIONS=1536
GEOND_EMBEDDING_MAX_CHARS=3000
```

Recommended for:

- first-time setup
- OSS demo recording
- benchmark baselines
- multilingual semantic retrieval when cloud calls are acceptable

### Azure OpenAI Service

Use this when you need Azure governance, regional deployment control, or Entra ID.

```env
GEOND_PRIVACY_MODE=redacted-cloud
GEOND_EMBEDDING_PROVIDER=azure-openai
GEOND_AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com
GEOND_AZURE_OPENAI_AUTH_MODE=api-key
GEOND_AZURE_OPENAI_API_KEY=<azure-openai-key>
GEOND_AZURE_OPENAI_API_VERSION=2024-10-21
GEOND_AZURE_OPENAI_EMBEDDING_DEPLOYMENT=text-embedding-small-prod
GEOND_EMBEDDING_DIMENSIONS=1536
```

For production, prefer Entra ID over API keys:

```env
GEOND_PRIVACY_MODE=redacted-cloud
GEOND_EMBEDDING_PROVIDER=azure-openai
GEOND_AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com
GEOND_AZURE_OPENAI_AUTH_MODE=entra-id
GEOND_AZURE_OPENAI_EMBEDDING_DEPLOYMENT=text-embedding-small-prod
GEOND_EMBEDDING_DIMENSIONS=1536
```

Recommended for:

- enterprise Azure users
- APIM gateway experiments
- managed identity and RBAC hardening
- cost tracking with Azure Cost Management
- regional compliance requirements

### Gateway Or APIM Mode

Use this when model calls should pass through a policy layer.

```env
GEOND_PRIVACY_MODE=redacted-cloud
GEOND_EMBEDDING_PROVIDER=gateway
GEOND_EMBEDDING_BASE_URL=https://<gateway-host>/v1
GEOND_EMBEDDING_API_KEY=<gateway-key>
GEOND_EMBEDDING_MODEL=text-embedding-3-small
GEOND_EMBEDDING_DIMENSIONS=1536
```

Recommended for:

- central rate limiting
- workspace or agent headers
- audit logging
- policy-based routing between deployments
- secret redaction before forwarding

### Local-Only Mode

Use this when development memory should not leave the machine.

```env
GEOND_PRIVACY_MODE=local-only
GEOND_EMBEDDING_PROVIDER=local-openai-compatible
GEOND_EMBEDDING_BASE_URL=http://localhost:1234/v1
GEOND_EMBEDDING_API_KEY=
GEOND_EMBEDDING_MODEL=<local-embedding-model>
GEOND_EMBEDDING_DIMENSIONS=<model-dimensions>
```

For Ollama-compatible setups:

```env
GEOND_PRIVACY_MODE=local-only
GEOND_EMBEDDING_PROVIDER=ollama
GEOND_EMBEDDING_MODEL=nomic-embed-text
GEOND_EMBEDDING_DIMENSIONS=768
```

Recommended for:

- private codebases
- offline demos
- teams that cannot send development memory to a cloud model
- local regression benchmarks

The VM validation used MiniLM directly rather than as an OpenAI-compatible
server. Its result is still useful: it proved a small multilingual embedding
model can retrieve across Korean and English probes, with observed MRR `0.8333`
in the sanitized Azure validation run.

## Selection Guide

| Situation | Recommended path | Why |
| --- | --- | --- |
| You want the fastest MVP setup | OpenAI baseline | Minimal infrastructure and matches the current schema dimensions. |
| You are already on Azure | Azure OpenAI | Fits enterprise identity, APIM, monitoring, and cost workflows. |
| You need gateway governance | APIM or another OpenAI-compatible gateway | Adds rate limits, routing, and centralized audit controls. |
| You cannot send memory off-device | Local-only SLM | Keeps imported chat, diffs, and snippets inside the local/private environment. |
| You are comparing retrieval quality | Run all providers with the same judgments file | Makes recall, MRR, nDCG, latency, and cost comparable. |
| You need multilingual recall | Prefer vector or hybrid modes | Korean queries can retrieve English comments/docs better than keyword search alone. |

## Performance And Reliability Notes

- Keep `keyword` mode available for exact file names, function names, and fallback
  searches without credentials.
- Use `hybrid` mode for agent-facing retrieval because it combines lexical
  precision with semantic recall.
- Record provider, model, dimensions, content hash, and benchmark label for every
  run so results remain comparable.
- Azure OpenAI capacity matters. The first capacity-1 smoke hit request-rate
  limits, so the script now uses capacity `7` for the small validation run.
- Local SLMs may spend more time on first model load than on encoding. Record
  load time and encode time separately.
- Windows console encoding can mangle multilingual output. Validation artifacts
  should write UTF-8 files or base64-wrapped JSON for VM command output.
- Provider/model dimension mismatches should fail early. The current schema is
  safest with 1536-dimensional providers until migrations are added.

## Benchmark Commands

Keyword baseline without credentials:

```bash
uv run geond benchmark-search app_context build_answer --mode keyword --repeat 5
```

Hybrid benchmark after configuring a provider:

```bash
uv run geond benchmark-search app_context "왜 service.py 파일이 바뀌었어?" \
    --mode hybrid \
    --repeat 3 \
    --judgments examples/benchmarks/search_judgments.json \
    --save \
    --label provider-hybrid
```

Compare saved runs:

```bash
uv run geond benchmark-report --format markdown
```

## Current Recommendation

For public OSS demos, keep OpenAI `text-embedding-3-small` as the baseline and
show local `keyword`, cloud `vector`, and `hybrid` retrieval side by side. For
Azure-focused validation, use Azure OpenAI behind the `azure-openai` provider and
record capacity, region, rate limits, and cleanup status. For privacy-sensitive
work, use `local-only` and a local embedding server, then compare quality against
the OpenAI/Azure baselines with the same judgments file.