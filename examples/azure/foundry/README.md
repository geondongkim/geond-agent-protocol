# Foundry Project-Managed Deployment Sample

This folder is a deployment skeleton for running Geond behind a Microsoft
Foundry hosted agent or project-managed workflow.

## Files

- `.foundry/agent-metadata.yaml`: local/dev metadata in the Foundry skill
  contract shape.
- `agent.yaml`: lightweight hosted-agent definition template.
- `project-managed-deployment.md`: operator runbook for provisioning, deploying,
  invoking, and evaluating.

## Expected Runtime Shape

1. APIM fronts Azure OpenAI or Foundry Models and applies the policy in
   `examples/azure/apim`.
2. The hosted agent container exposes a health endpoint and connects to Geond's
   Postgres database.
3. Geond uses `GEOND_EMBEDDING_PROVIDER=gateway` so model calls inherit APIM
   token limits, metrics, and cache policy.
4. Evaluation suites in `.foundry/agent-metadata.yaml` point to retrieval-quality
   datasets generated from imported Copilot, Codex, and Claude Code sessions.

Official references:

- <https://learn.microsoft.com/azure/ai-foundry/agents/how-to/deploy-hosted-agent>
- <https://learn.microsoft.com/azure/foundry/agents/concepts/hosted-agent-permissions>
- <https://learn.microsoft.com/azure/ai-foundry/reference/foundry-project-rest-preview>
