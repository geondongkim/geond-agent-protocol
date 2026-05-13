# Azure validation artifacts

This directory stores sanitized evidence from temporary Azure validation runs.

Each run should use a single tagged resource group and must delete that group at the end of the run. Public artifacts should not include subscription IDs, tenant IDs, access keys, tokens, or raw request payloads that contain secrets.

Latest validation evidence:

![Geond Azure validation](20260512-combined/geond_azure_validation.gif)

Typical files:

- `summary.json`: resource names, SKUs, durations, step statuses, and cleanup status
- `README.md`: human-readable run summary
- `azure_openai_benchmark.md`: Geond retrieval benchmark through Azure OpenAI embeddings
- `slm_vm_benchmark.json`: local multilingual embedding benchmark from the temporary VM
- `geond_azure_validation.gif`: visual evidence summary generated from the run artifacts

Run the smoke validation from the repository root:

```powershell
.\scripts\azure_validation_smoke.ps1
```

For a cheaper partial pass, skip slow resources explicitly:

```powershell
.\scripts\azure_validation_smoke.ps1 -SkipApim -SkipVm
```

For step-by-step Azure CLI commands, Azure Portal instructions, AWS/GCP resource
analogues, and cost-signal guidance, see
[docs/deployment_guide.md](../deployment_guide.md).

For the two-client team collaboration scenario, see
[team_collab_validation.md](team_collab_validation.md). That flow uses Azure
Database for PostgreSQL Flexible Server as shared Geond memory while each
developer still runs `geond-mcp`, CLI, and the dashboard locally.
