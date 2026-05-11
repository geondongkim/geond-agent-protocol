# Embedding Configuration

Geond can run without embeddings. The MVP starts with keyword and metadata search, then adds vector retrieval when an embedding provider is configured.

## 1. What I Need From You

You do not need to paste secrets into chat. Put them in a local `.env` file copied from `.env.example`.

Required decisions:

| Decision | Why it matters | Example |
|---|---|---|
| Provider | Determines API shape and auth | `none`, `openai`, `openai-compatible`, `github-models`, later `local` |
| Model | Determines quality, cost, and vector dimensions | `text-embedding-3-small` |
| Dimensions | Must match DB vector column in the MVP | `1536` |
| API key | Needed for cloud providers | `GEOND_EMBEDDING_API_KEY=...` |
| Base URL | Needed for OpenAI-compatible hosts | `https://api.openai.com/v1` or provider-specific |
| Privacy mode | Whether raw text may leave the machine | local-only, redacted-cloud, cloud-ok |

## 2. Recommended MVP Setup

Start with embeddings disabled.

```env
GEOND_EMBEDDING_PROVIDER=none
```

This keeps the first importer and MCP tools local-only. It is enough for:

- parsing VS Code Copilot Chat sessions
- storing messages and events
- keyword search
- explaining file changes from imported file snapshots
- testing MCP connectivity

Then enable embeddings only after redaction and data retention choices are clear.

## 3. OpenAI-Compatible Provider

Use this for OpenAI, GitHub Models if exposed through an OpenAI-compatible endpoint, local OpenAI-compatible gateways, or other compatible services.

```env
GEOND_EMBEDDING_PROVIDER=openai-compatible
GEOND_EMBEDDING_MODEL=text-embedding-3-small
GEOND_EMBEDDING_BASE_URL=https://api.openai.com/v1
GEOND_EMBEDDING_API_KEY=your_api_key_here
GEOND_EMBEDDING_DIMENSIONS=1536
```

Install optional dependencies:

```bash
pip install -e .[embeddings]
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

Before locking this in, verify the exact embedding model and endpoint from the current GitHub Models catalog.

## 5. Azure OpenAI or Microsoft Foundry

If you want production-grade deployment later, Microsoft Foundry/Azure OpenAI can host embedding models.

Information needed:

- endpoint
- deployment name
- API version
- auth mode: API key or Entra ID
- embedding dimensions
- regional/compliance constraints

The MVP code currently implements OpenAI-compatible configuration. Azure-specific auth should be added as a provider adapter instead of overloading the basic provider.

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
- Keep `GEOND_EMBEDDING_PROVIDER=none` until the redaction policy is implemented.
- Store the embedding model and content hash with every vector.
- Make workspace purge delete embeddings too.

## 9. Practical Recommendation

For the next implementation step:

1. Keep embeddings disabled.
2. Finish keyword search and import flow.
3. Add redaction tests.
4. Add OpenAI-compatible embeddings behind an explicit command.
5. Add local embeddings as the privacy-friendly default once retrieval quality is proven.
