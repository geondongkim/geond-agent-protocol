# Project-Managed Deployment Runbook

Use this when Geond should be validated inside a Microsoft Foundry project
instead of only as a local MCP server.

## 1. Provision Project

Create or select a Foundry project with:

- Foundry project endpoint.
- Application Insights.
- Managed identity.
- Azure Container Registry if using hosted agents.
- APIM AI gateway if model calls should be governed centrally.

Record the endpoint and registry in `.foundry/agent-metadata.yaml`.

## 2. Build And Push Container

```bash
docker build -t geond-memory-agent:dev .
az acr login --name <registry>
docker tag geond-memory-agent:dev <registry>.azurecr.io/geond-memory-agent:<tag>
docker push <registry>.azurecr.io/geond-memory-agent:<tag>
```

Grant the Foundry project managed identity pull access to the registry.

## 3. Deploy Hosted Agent

Use `agent.yaml` as the deployment template and keep these values environment
specific:

- `GEOND_DATABASE_URL`
- `GEOND_EMBEDDING_PROVIDER`
- `GEOND_EMBEDDING_BASE_URL`
- `GEOND_EMBEDDING_API_KEY` or managed identity configuration
- `GEOND_PRIVACY_MODE`

For project-managed inference through APIM, set:

```bash
GEOND_EMBEDDING_PROVIDER=gateway
GEOND_EMBEDDING_BASE_URL=https://<apim-name>.azure-api.net/openai/v1
GEOND_PRIVACY_MODE=redacted-cloud
```

## 4. Smoke Test

Run these before publishing the demo:

```bash
uv run geond seed-sample
uv run geond index-tree-sitter src --workspace-uri file:///sample/geond --workspace-name geond
uv run geond benchmark-search app_context build_answer \
  --mode keyword \
  --judgments examples/benchmarks/search_judgments.json \
  --format markdown
```

## 5. Evaluate

Use the `retrieval-smoke` suite in `.foundry/agent-metadata.yaml` for a first
quality gate:

- `recall_at_k` should stay at `1.0` for the seed dataset.
- `mrr` should not regress when switching providers or gateway policies.
- `ndcg_at_k` should be compared across OpenAI, Azure OpenAI, gateway, and local
  providers before adding larger public demos.

## 6. Rollback

Keep the previous container tag and benchmark run id in the deployment notes.
Rollback is valid when any of these fail:

- Hosted agent status is not `Started`.
- APIM returns token-limit or content-safety errors for the smoke prompts.
- `benchmark-report --format markdown` shows lower recall or MRR than the
  previous accepted run.
