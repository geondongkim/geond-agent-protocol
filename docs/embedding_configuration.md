# Embedding Configuration

Geond can run without embeddings, but the MVP intentionally uses embeddings so keyword-only retrieval can be compared with vector and hybrid retrieval.

## 1. What I Need From You

You do not need to paste secrets into chat. Put them in a local `.env` file copied from `.env.example`.

Required decisions:

| Decision | Why it matters | Example |
|---|---|---|
| Provider | Determines API shape and auth | `openai`, `azure-openai`, `gateway`, `local-openai-compatible`, `ollama` |
| Model | Determines quality, cost, multilingual behavior, and vector dimensions | `text-embedding-3-small` |
| Dimensions | Must match DB vector column in the MVP | `1536` |
| API key | Needed for cloud providers | `GEOND_EMBEDDING_API_KEY=...` |
| Base URL | Needed for OpenAI-compatible hosts | `https://api.openai.com/v1` or provider-specific |
| Privacy mode | Whether raw text may leave the machine | local-only, redacted-cloud, cloud-ok |

## 2. Recommended MVP Setup

Start with OpenAI embeddings enabled.

```env
GEOND_EMBEDDING_PROVIDER=openai
GEOND_EMBEDDING_MODEL=text-embedding-3-small
GEOND_EMBEDDING_BASE_URL=
GEOND_EMBEDDING_API_KEY=your_api_key_here
GEOND_EMBEDDING_DIMENSIONS=1536
GEOND_EMBEDDING_MAX_CHARS=3000
```

Leave `GEOND_EMBEDDING_BASE_URL` empty for the default OpenAI endpoint. Set it only for OpenAI-compatible gateways or non-default hosts.

`GEOND_EMBEDDING_MAX_CHARS` keeps very large tool outputs under provider token limits. The raw message can still be stored locally; the embedding request uses the truncated text.

This enables:

- parsing VS Code Copilot Chat sessions
- storing messages and events
- keyword search
- vector search
- hybrid keyword + vector search
- explaining file changes from imported file snapshots
- testing MCP connectivity

## 3. OpenAI-Compatible Provider

Use this for OpenAI, GitHub Models if exposed through an OpenAI-compatible endpoint, local OpenAI-compatible gateways, or other compatible services.

```env
GEOND_EMBEDDING_PROVIDER=openai-compatible
GEOND_EMBEDDING_MODEL=text-embedding-3-small
GEOND_EMBEDDING_BASE_URL=https://api.openai.com/v1
GEOND_EMBEDDING_API_KEY=your_api_key_here
GEOND_EMBEDDING_DIMENSIONS=1536
```

Install dependencies with uv:

```bash
uv sync
```

## 4. GitHub Models

GitHub Models may be useful for development if you want to keep auth under your GitHub account.

Information needed:

- GitHub token with model access
- model id from the GitHub Models catalog
- endpoint/base URL used by the SDK
- embedding dimensions for that model

Potential env shape:

```env
GEOND_EMBEDDING_PROVIDER=github-models
GEOND_EMBEDDING_MODEL=<model-id>
GEOND_EMBEDDING_BASE_URL=https://models.github.ai/inference/
GEOND_EMBEDDING_API_KEY=<github-token>
GEOND_EMBEDDING_DIMENSIONS=<model-dimensions>
```

Before locking this in, verify the exact embedding model and endpoint from the current GitHub Models catalog. GitHub Models is not the MVP default.

## 5. Azure OpenAI or Microsoft Foundry

If you want production-grade deployment later, Microsoft Foundry/Azure OpenAI can host embedding models.

Information needed:

- endpoint
- deployment name
- API version
- auth mode: API key or Entra ID
- embedding dimensions
- regional/compliance constraints

The MVP now includes an Azure OpenAI embedding adapter using API-key or Entra ID auth:

```env
GEOND_EMBEDDING_PROVIDER=azure-openai
GEOND_AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com
GEOND_AZURE_OPENAI_API_KEY=<key>
GEOND_AZURE_OPENAI_AUTH_MODE=api-key
GEOND_AZURE_OPENAI_API_VERSION=2024-10-21
GEOND_AZURE_OPENAI_EMBEDDING_DEPLOYMENT=<deployment-name>
GEOND_EMBEDDING_DIMENSIONS=1536
```

For Entra ID:

```env
GEOND_EMBEDDING_PROVIDER=azure-openai
GEOND_AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com
GEOND_AZURE_OPENAI_AUTH_MODE=entra-id
GEOND_AZURE_OPENAI_EMBEDDING_DEPLOYMENT=<deployment-name>
GEOND_EMBEDDING_DIMENSIONS=1536
```

Foundry project-managed model deployment is still a future hardening item.

## 6. Local Embeddings

Local embeddings are the best privacy default for developer machines. Geond supports local OpenAI-compatible endpoints without requiring an API key.

Good future options:

- sentence-transformers
- Ollama embeddings through the OpenAI-compatible endpoint
- llama.cpp-compatible embedding endpoint
- local OpenAI-compatible gateway

Information needed:

- model name
- runtime command or base URL
- dimensions
- CPU/GPU constraints

Example:

```env
GEOND_PRIVACY_MODE=local-only
GEOND_EMBEDDING_PROVIDER=local-openai-compatible
GEOND_EMBEDDING_BASE_URL=http://localhost:1234/v1
GEOND_EMBEDDING_MODEL=local-embedding-model
GEOND_EMBEDDING_DIMENSIONS=768
```

For Ollama-style setups:

```env
GEOND_PRIVACY_MODE=local-only
GEOND_EMBEDDING_PROVIDER=ollama
GEOND_EMBEDDING_MODEL=nomic-embed-text
GEOND_EMBEDDING_DIMENSIONS=768
```

## 7. Schema Dimension Caveat

The initial schema uses:

```sql
embedding vector(1536)
```

That matches common 1536-dimensional embedding models. If you choose a different dimension, update `schemas/001_initial.sql` before creating the database, or add a migration later.

## 8. Safety Rules

- Do not embed raw secrets.
- Run redaction before external API calls.
- Use `redacted-cloud` or `cloud-ok` mode knowingly: text sent for embeddings leaves the machine.
- Use `local-only` to block cloud embedding providers before a network call.
- Store the embedding model and content hash with every vector.
- Make workspace purge delete embeddings too.

## 9. Practical Recommendation

For the next implementation step:

1. Use OpenAI `text-embedding-3-small` for the first baseline.
2. Compare `keyword`, `vector`, and `hybrid` retrieval on the same imported sessions.
3. Add redaction tests.
4. Benchmark OpenAI, Azure OpenAI, gateway, and local providers on the same fixture queries.
5. Expand provider comparison reports with quality metrics, cost, and token accounting.
