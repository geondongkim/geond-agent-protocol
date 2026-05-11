# Provider Extensions

The MVP defaults to OpenAI embeddings with `text-embedding-3-small`. The provider layer now supports cloud, gateway, and local OpenAI-compatible endpoints behind the same retrieval pipeline.

## Provider Matrix

| Provider | `GEOND_EMBEDDING_PROVIDER` | Network posture | Notes |
| --- | --- | --- | --- |
| OpenAI | `openai` | Cloud | Uses OpenAI SDK default endpoint unless `GEOND_EMBEDDING_BASE_URL` is set. |
| OpenAI-compatible gateway | `openai-compatible` or `gateway` | Usually cloud or private network | Set `GEOND_EMBEDDING_BASE_URL` and `GEOND_EMBEDDING_API_KEY`. |
| GitHub Models | `github-models` | Cloud | Defaults base URL to `https://models.github.ai/inference`. |
| Azure OpenAI | `azure-openai` | Cloud | Uses Azure endpoint, API version, and deployment name. |
| Local OpenAI-compatible | `local-openai-compatible` | Local/private | Requires `GEOND_EMBEDDING_BASE_URL`; allowed with `local-only`. |
| Ollama OpenAI-compatible | `ollama` | Local | Defaults base URL to `http://localhost:11434/v1`; set model and dimensions. |
| Disabled | `none` or `disabled` | No embedding calls | Keyword search remains available. |

## OpenAI

```env
GEOND_PRIVACY_MODE=redacted-cloud
GEOND_EMBEDDING_PROVIDER=openai
GEOND_EMBEDDING_MODEL=text-embedding-3-small
GEOND_EMBEDDING_API_KEY=sk-...
GEOND_EMBEDDING_DIMENSIONS=1536
```

## Azure OpenAI

`GEOND_AZURE_OPENAI_EMBEDDING_DEPLOYMENT` is the Azure deployment name.

```env
GEOND_PRIVACY_MODE=redacted-cloud
GEOND_EMBEDDING_PROVIDER=azure-openai
GEOND_AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com
GEOND_AZURE_OPENAI_API_KEY=<key>
GEOND_AZURE_OPENAI_AUTH_MODE=api-key
GEOND_AZURE_OPENAI_API_VERSION=2024-10-21
GEOND_AZURE_OPENAI_EMBEDDING_DEPLOYMENT=text-embedding-small-prod
GEOND_EMBEDDING_DIMENSIONS=1536
```

For Entra ID authentication, leave the key empty and use `DefaultAzureCredential`:

```env
GEOND_PRIVACY_MODE=redacted-cloud
GEOND_EMBEDDING_PROVIDER=azure-openai
GEOND_AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com
GEOND_AZURE_OPENAI_AUTH_MODE=entra-id
GEOND_AZURE_OPENAI_EMBEDDING_DEPLOYMENT=text-embedding-small-prod
GEOND_EMBEDDING_DIMENSIONS=1536
```

The token scope is `https://cognitiveservices.azure.com/.default`. Configure `az login`,
managed identity, workload identity, or environment credentials before running embedding
commands.

## Gateway Or OpenAI-Compatible

Use this mode for private AI gateways, self-hosted proxies, or OpenAI-compatible services that still require an API key.

```env
GEOND_PRIVACY_MODE=redacted-cloud
GEOND_EMBEDDING_PROVIDER=gateway
GEOND_EMBEDDING_BASE_URL=https://gateway.example.com/v1
GEOND_EMBEDDING_API_KEY=<gateway-key>
GEOND_EMBEDDING_MODEL=text-embedding-3-small
GEOND_EMBEDDING_DIMENSIONS=1536
```

Recommended gateway policy:

- Require workspace/project headers.
- Apply per-agent and per-workspace rate limits.
- Log model, workspace id, latency, and status.
- Block or redact obvious secret patterns before forwarding.
- Route deployments from `GEOND_EMBEDDING_MODEL`.

An Azure API Management version of this policy is available at
[examples/azure/apim/geond-openai-gateway-policy.xml](../examples/azure/apim/geond-openai-gateway-policy.xml).

## Azure Validation Smoke

The repository includes a repeatable smoke script for real Azure validation:

```powershell
.\scripts\azure_validation_smoke.ps1
```

The script creates a tagged temporary resource group, validates Azure OpenAI
embeddings, APIM Consumption gateway scaffolding, and a B2s VM local multilingual
embedding benchmark, then deletes the resource group. APIM policy application is
opt-in because it can be long-running:

```powershell
.\scripts\azure_validation_smoke.ps1 -ApplyApimPolicy
```

Sanitized evidence from the latest run is stored in
[docs/azure_validation/20260512-combined](azure_validation/20260512-combined).

For the full deployment walkthrough, including Azure Portal steps and AWS/GCP
resource analogues, see [docs/deployment_guide.md](deployment_guide.md).

## Local-Only

Local-only blocks cloud providers before a network call is made.

```env
GEOND_PRIVACY_MODE=local-only
GEOND_EMBEDDING_PROVIDER=local-openai-compatible
GEOND_EMBEDDING_BASE_URL=http://localhost:1234/v1
GEOND_EMBEDDING_API_KEY=
GEOND_EMBEDDING_MODEL=local-embedding-model
GEOND_EMBEDDING_DIMENSIONS=768
```

For Ollama-compatible setups:

```env
GEOND_PRIVACY_MODE=local-only
GEOND_EMBEDDING_PROVIDER=ollama
GEOND_EMBEDDING_MODEL=nomic-embed-text
GEOND_EMBEDDING_DIMENSIONS=768
```

## Benchmarking Providers

Use keyword mode without credentials:

```bash
uv run geond benchmark-search app_context --mode keyword --repeat 5
```

Use vector or hybrid mode after configuring a provider:

```bash
uv run geond benchmark-search app_context build_answer \
    --mode hybrid \
    --repeat 3 \
    --judgments examples/benchmarks/search_judgments.json \
    --save \
    --label gateway-hybrid
```

Compare saved runs:

```bash
uv run geond benchmark-report --format markdown
```
