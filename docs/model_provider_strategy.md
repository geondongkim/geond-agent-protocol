# Model and Provider Strategy

This document records the MVP model choices and the provider expansion plan.

## 1. MVP Defaults

| Use | Model | Provider | Notes |
|---|---|---|---|
| Embeddings | `text-embedding-3-small` | OpenAI | 1536 dimensions, multilingual semantic retrieval |
| Deep reasoning | `gpt-5.4` | OpenAI | Use for complex architecture, evaluation synthesis, difficult debugging |
| Balanced coding work | `gpt-5.4-mini` | OpenAI | Use for implementation, summarization, parser improvements |
| Fast/cheap tasks | `gpt-5.4-nano` | OpenAI | Use for classification, intent tagging, short summaries, batch metadata |

The MVP uses OpenAI cloud APIs. Local-only mode is intentionally deferred so the first version can compare keyword retrieval vs vector retrieval quickly.

## 2. Privacy Modes To Support Later

| Mode | Meaning | MVP Status |
|---|---|---|
| `cloud-ok` | Raw or redacted text can be sent to configured cloud APIs | MVP default for embedding experiments |
| `redacted-cloud` | Redaction runs before any external API call | Next privacy milestone |
| `local-only` | No text leaves the machine; local embeddings/LLMs only | Later milestone |

The code should keep provider boundaries explicit so these modes can be enforced centrally.

## 3. OpenAI Configuration

The `.env` file should contain:

```env
GEOND_EMBEDDING_PROVIDER=openai
GEOND_EMBEDDING_MODEL=text-embedding-3-small
GEOND_EMBEDDING_DIMENSIONS=1536
GEOND_EMBEDDING_API_KEY=...
```

`GEOND_EMBEDDING_BASE_URL` should normally be left empty for OpenAI. The OpenAI SDK then uses its default endpoint, currently `https://api.openai.com/v1`. Only set a base URL for OpenAI-compatible gateways or non-default hosts.

If chat-model calls are added during implementation or verification, set:

```env
OPENAI_API_KEY=...
GEOND_LLM_MODEL_REASONING=gpt-5.4
GEOND_LLM_MODEL_BALANCED=gpt-5.4-mini
GEOND_LLM_MODEL_FAST=gpt-5.4-nano
```

For convenience, `OPENAI_API_KEY` may use the same value as `GEOND_EMBEDDING_API_KEY` when the same OpenAI account/project is used.

## 4. Multilingual Requirements

The repository and docs can stay English-first for public distribution, but stored development memory must support multilingual content.

Requirements:

- Read JSON/JSONL/Markdown as UTF-8.
- Emit JSON with `ensure_ascii=False` where CLI output is intended for humans.
- Avoid lossy normalization of Korean, Japanese, Chinese, accented Latin text, emoji, or mixed-language code comments.
- Use multilingual-capable embedding models.
- Keep keyword search as a fallback, but expect lexical search to be weaker for languages without whitespace tokenization.
- Prefer vector retrieval for cross-lingual matching, for example Korean chat questions retrieving English code comments or docs.

`text-embedding-3-small` is the MVP embedding model because it gives strong multilingual semantic retrieval at a practical cost and has 1536-dimensional vectors that match the initial schema.

## 5. Provider Expansion Plan

### OpenAI

First-class MVP provider.

Use cases:

- embeddings
- future summaries
- future intent classification
- future evaluation generation

### Azure OpenAI / Microsoft Foundry

Expected future provider.

Information needed later:

- Azure/OpenAI endpoint
- deployment names for embedding and chat models
- API version
- auth mode: API key or Entra ID
- region and data boundary requirements
- model dimensions

Implementation note: add a dedicated provider adapter instead of overloading the OpenAI default path, because Azure deployment names and API versions differ from vanilla OpenAI.

### Local Providers

Expected later provider family.

Candidates:

- Ollama embeddings
- sentence-transformers
- llama.cpp-compatible embedding endpoint
- local OpenAI-compatible gateway

Local providers matter for `local-only` privacy mode and offline development, but they should come after the OpenAI MVP comparison baseline.

### Other Hosted Providers To Evaluate Later

| Provider | Why consider it |
|---|---|
| Cohere Embed | Strong multilingual/business retrieval options |
| Voyage AI | Strong retrieval-oriented embeddings, good code/search options |
| Jina AI embeddings | Multilingual and open embedding model ecosystem |
| Google Gemini embeddings | Useful if users already run Google AI infrastructure |
| Anthropic | No general embeddings focus today, but relevant for MCP/client ecosystem |
| Hugging Face Inference / TEI | Good path for self-hostable or open embeddings |

## 6. Model Comparison Plan

The project should eventually compare providers and models on the same stored development memory.

Minimum benchmark set:

- Korean query -> English code/comment retrieval
- English query -> Korean chat/session retrieval
- “Why did this file change?” over chat + file snapshot evidence
- Similar bug retrieval across sessions
- Symbol-related retrieval where AST context should improve ranking
- Cost and latency per 1,000 messages embedded

Metrics:

- recall@k
- MRR
- nDCG@k
- answer faithfulness from cited evidence
- embedding cost
- indexing latency
- query latency

## 7. Current Recommendation

For the MVP:

1. Use OpenAI `text-embedding-3-small` immediately.
2. Keep `keyword`, `vector`, and `hybrid` retrieval modes side-by-side.
3. Build a small benchmark from imported Copilot Chat sessions.
4. Add Azure OpenAI after the OpenAI baseline works.
5. Add local embeddings after redaction and provider boundaries are stable.

## 8. MVP Verification Note

On the first imported VS Code Copilot Chat session, a Korean query was tested across retrieval modes:

```text
왜 채팅 세션 복원이 안됐어?
```

Observed result:

| Mode | Result |
|---|---|
| `keyword` | No result for the exact phrasing |
| `vector` | Retrieved the relevant Korean chat session about missing/restored chat records |
| `hybrid` | Retrieved the same vector-backed evidence while preserving keyword mode for exact matches |

This confirms why embeddings are part of the MVP: multilingual semantic retrieval catches related context that lexical search misses.
