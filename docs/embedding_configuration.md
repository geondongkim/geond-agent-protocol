# Embedding Configuration

Geond can run without embeddings, but the MVP intentionally uses embeddings so keyword-only retrieval can be compared with vector and hybrid retrieval.

## 1. What I Need From You

You do not need to paste secrets into chat. Put them in a local `.env` file copied from `.env.example`.

Required decisions:

| Decision | Why it matters | Example |
|---|---|---|
| Provider | Determines API shape and auth | MVP: `openai`; later: `azure-openai`, `openai-compatible`, `local` |
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

The MVP code currently implements OpenAI/OpenAI-compatible configuration. Azure-specific auth should be added as a provider adapter instead of overloading the basic provider.

## 6. Local Embeddings

Local embeddings are the best privacy default for developer machines, but they add packaging and performance work.

Good future options:

- sentence-transformers
- Ollama embeddings
- llama.cpp-compatible embedding endpoint
- local OpenAI-compatible gateway

Information needed:

- model name
- runtime command or base URL
- dimensions
- CPU/GPU constraints

## 7. Schema Dimension Caveat

The initial schema uses:

```sql
embedding vector(1536)
```

That matches common 1536-dimensional embedding models. If you choose a different dimension, update `schemas/001_initial.sql` before creating the database, or add a migration later.

## 8. Safety Rules

- Do not embed raw secrets.
- Run redaction before external API calls.
- Use `cloud-ok` mode knowingly for the MVP: text sent for embeddings leaves the machine.
- Store the embedding model and content hash with every vector.
- Make workspace purge delete embeddings too.

## 9. Practical Recommendation

For the next implementation step:

1. Use OpenAI `text-embedding-3-small` for the first baseline.
2. Compare `keyword`, `vector`, and `hybrid` retrieval on the same imported sessions.
3. Add redaction tests.
4. Add Azure OpenAI as the next hosted provider.
5. Add local embeddings as the privacy-friendly mode once provider boundaries are stable.
