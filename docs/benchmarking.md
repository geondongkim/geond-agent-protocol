# Benchmarking

Geond includes a small retrieval benchmark command for local smoke tests and provider comparisons. It is intentionally simple: it measures end-to-end query latency from the CLI process through database retrieval, and includes embedding time for vector and hybrid modes.

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

## Cleanup

```bash
uv run geond purge-workspace file:///sample/geond --yes
```

## Current Limitations

- The command is a development benchmark, not a statistically rigorous performance suite.
- It does not yet persist benchmark runs.
- It does not yet collect token counts, provider billing dimensions, or concurrent throughput.
