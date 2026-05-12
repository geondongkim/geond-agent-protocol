# Benchmarking

Geond includes a retrieval benchmark command for local smoke tests and provider
comparisons. It measures end-to-end query latency from the CLI process through
database retrieval, includes embedding time for vector and hybrid modes, and can
score retrieval quality when a small judgments file is provided.

## Prepare Data

```bash
docker compose up -d postgres
docker compose --profile tools run --rm geond-migrate
uv run geond seed-sample
```

## Keyword Baseline

Keyword mode does not require embedding credentials.

```bash
uv run geond benchmark-search app_context service.py --mode keyword --repeat 5 --workspace-uri file:///sample/geond
```

Example output shape:

```json
{
  "mode": "keyword",
  "repeat": 5,
  "limit": 10,
  "queries": [
    {
      "query": "app_context",
      "result_count": 1,
      "min_ms": 1.2,
      "avg_ms": 1.7,
      "max_ms": 2.4
    }
  ]
}
```

## Vector And Hybrid

Vector and hybrid modes require an embedding provider. The measured time includes the provider call.

```bash
uv run geond benchmark-search app_context build_answer --mode hybrid --repeat 3
```

Use [provider_extensions.md](provider_extensions.md) to switch between OpenAI, Azure OpenAI, gateway, and local OpenAI-compatible providers.

## Quality Judgments

Pass a judgments file to calculate `recall_at_k`, `mrr`, and `ndcg_at_k` for
each query:

```bash
uv run geond benchmark-search app_context build_answer \
    --mode keyword \
    --workspace-uri file:///sample/geond \
    --judgments examples/benchmarks/search_judgments.json \
    --include-results
```

Judgments can match exact message/session/source/workspace fields or snippets:

```json
{
  "queries": [
    {
      "query": "app_context",
      "expected": [
        {
          "source": "seed",
          "snippet_contains": "app_context"
        }
      ]
    }
  ]
}
```

Use `--format markdown` when collecting results for a report or README snippet.
When `--include-results` is enabled, each top result includes score diagnostics
for available retrieval signals: `fts_rank`, `trigram_score`, `vector_score`,
and `hybrid_score`.

For Korean/English mixed retrieval checks, use the multilingual fixture:

```bash
uv run geond benchmark-search "왜 service.py 파일이 바뀌었어?" \
  "database initialization app_context" \
  --mode keyword \
  --workspace-uri file:///sample/geond \
  --judgments examples/benchmarks/multilingual_search_judgments.json \
  --include-results
```

## Saved Runs

Persist a run:

```bash
uv run geond benchmark-search app_context \
    --mode keyword \
    --repeat 5 \
    --workspace-uri file:///sample/geond \
    --save \
    --label keyword-baseline
```

Compare saved runs:

```bash
uv run geond benchmark-report --workspace-uri file:///sample/geond --format markdown
```

`--workspace-uri` accepts the canonical root URI or any registered workspace
alias, so benchmark history remains queryable after a folder move.

## Cleanup

```bash
uv run geond purge-workspace file:///sample/geond --yes
```

## Current Limitations

- The command is a development benchmark, not a statistically rigorous performance suite.
- It does not yet collect provider billing dimensions or concurrent throughput.
- Token counts are available when the provider/gateway exposes them, but Geond
  does not yet normalize them across providers.
