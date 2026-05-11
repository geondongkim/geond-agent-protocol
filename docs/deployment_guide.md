# Deployment Guide

Geond is local-first: the core protocol runs on a developer machine with
Postgres, pgvector, the CLI, and the MCP server. Azure is optional and is used
for provider validation, AI gateway experiments, hosted embeddings, and
multilingual local-model benchmarks on disposable compute.

This guide documents both the CLI path and the Azure Portal path so contributors
who are new to Azure can reproduce the validation without learning every Azure
concept first.

## Verified Tool Versions

The latest validation pass was run on Windows with:

| Tool | Version |
| --- | --- |
| Azure CLI | `2.85.0` |
| uv | `0.11.1` |
| Project Python through `uv run python` | `3.11.15` |
| Docker | `29.2.1` |

The host `python` command may point to a different interpreter. Use `uv run` for
project commands so the pinned project environment is used.

## Resource Map For AWS And GCP Users

| Geond concern | Azure resource | AWS analogue | GCP analogue | Why it matters |
| --- | --- | --- | --- | --- |
| Temporary deployment boundary | Resource group | CloudFormation stack plus tagged resources | Project or folder plus labels | Delete the group after validation to remove all child resources together. |
| Hosted embedding model | Azure OpenAI resource and model deployment | Amazon Bedrock model invocation or SageMaker endpoint | Vertex AI model endpoint | Provides cloud embeddings for vector and hybrid retrieval. |
| AI gateway | Azure API Management | API Gateway plus usage plans and authorizers | API Gateway or Apigee | Centralizes routing, rate limits, header checks, and policy enforcement. |
| API backend definition | APIM backend | API Gateway integration target | API Gateway backend or Apigee target server | Lets gateway policy route requests to a model endpoint or another service. |
| Local SLM benchmark compute | Azure Virtual Machine | EC2 instance | Compute Engine VM | Runs multilingual embedding benchmarks without using a hosted model endpoint. |
| Identity | Microsoft Entra ID and managed identity | IAM role or IAM Identity Center | IAM service account | Preferred auth path for production; avoids long-lived keys. |
| Secret storage | Azure Key Vault | AWS Secrets Manager or Parameter Store | Secret Manager | Store model keys and gateway secrets outside `.env` for hosted workloads. |
| Monitoring | Azure Monitor and Log Analytics | CloudWatch | Cloud Logging and Cloud Monitoring | Records API, VM, and cost/usage signals for later review. |
| Cost grouping | Tags and Cost Management | Cost allocation tags and Cost Explorer | Labels and Billing export | Tags make validation resources visible and auditable. |

## Current Validation Topology

The smoke validation creates one tagged resource group and places all temporary
resources inside it:

```text
rg-geond-validate-<timestamp>
├── Azure OpenAI S0 account
│   └── text-embedding-3-small GlobalStandard deployment
├── API Management Consumption instance
│   ├── geond-openai API
│   └── three backend definitions
└── Standard_B2s Ubuntu VM
    └── multilingual MiniLM benchmark run-command
```

The default smoke path deletes the resource group in a `finally` block and waits
for deletion. Public evidence from the latest run is in
[docs/azure_validation/20260512-combined](azure_validation/20260512-combined).

## CLI Deployment Path

Use the scripted path for repeatable validation:

```powershell
az login
az account show
.\scripts\azure_validation_smoke.ps1
```

For a partial pass that avoids slower or more expensive resources:

```powershell
.\scripts\azure_validation_smoke.ps1 -SkipApim -SkipVm
```

APIM policy application is available but opt-in because policy REST updates can
be long-running in some subscriptions:

```powershell
.\scripts\azure_validation_smoke.ps1 -ApplyApimPolicy
```

### What The Script Does

1. Creates a single `rg-geond-validate-*` resource group with validation tags.
2. Creates an Azure OpenAI S0 account.
3. Deploys `text-embedding-3-small` as `GlobalStandard` with capacity `7`.
4. Runs Geond against the Azure OpenAI embedding provider.
5. Creates an APIM Consumption instance, three backends, and the `geond-openai`
   API scaffold.
6. Creates a `Standard_B2s` Ubuntu VM and runs a multilingual MiniLM benchmark.
7. Writes sanitized local artifacts under `docs/azure_validation/`.
8. Deletes the resource group and waits for it to disappear.

### Manual CLI Skeleton

The script is the source of truth, but the manual equivalent is useful for
debugging one resource at a time. Replace placeholder names before running.

```powershell
$Location = "koreacentral"
$RunId = Get-Date -Format "yyyyMMddHHmmss"
$ResourceGroup = "rg-geond-validate-$RunId"
$Tags = @("project=geond-agent-protocol", "purpose=validation", "deleteAfter=today")

az group create --name $ResourceGroup --location $Location --tags $Tags

az provider register --namespace Microsoft.CognitiveServices
az provider register --namespace Microsoft.ApiManagement
az provider register --namespace Microsoft.Compute
```

Create Azure OpenAI and deploy the embedding model:

```powershell
$OpenAiName = "<globally-unique-openai-name>"
$DeploymentName = "text-embedding-small-prod"

az cognitiveservices account create `
  --name $OpenAiName `
  --resource-group $ResourceGroup `
  --location $Location `
  --kind OpenAI `
  --sku S0 `
  --custom-domain $OpenAiName

az cognitiveservices account deployment create `
  --name $OpenAiName `
  --resource-group $ResourceGroup `
  --deployment-name $DeploymentName `
  --model-name text-embedding-3-small `
  --model-format OpenAI `
  --model-version 1 `
  --sku-name GlobalStandard `
  --sku-capacity 7
```

Configure Geond for Azure OpenAI:

```powershell
$env:GEOND_PRIVACY_MODE = "redacted-cloud"
$env:GEOND_EMBEDDING_PROVIDER = "azure-openai"
$env:GEOND_AZURE_OPENAI_ENDPOINT = az cognitiveservices account show `
  --resource-group $ResourceGroup `
  --name $OpenAiName `
  --query properties.endpoint `
  -o tsv
$env:GEOND_AZURE_OPENAI_API_KEY = az cognitiveservices account keys list `
  --resource-group $ResourceGroup `
  --name $OpenAiName `
  --query key1 `
  -o tsv
$env:GEOND_AZURE_OPENAI_AUTH_MODE = "api-key"
$env:GEOND_AZURE_OPENAI_EMBEDDING_DEPLOYMENT = $DeploymentName
$env:GEOND_EMBEDDING_DIMENSIONS = "1536"

docker compose up -d postgres
docker compose --profile tools run --rm geond-migrate
uv run geond seed-sample
uv run geond embed-messages --limit 10
uv run geond benchmark-search app_context "왜 service.py 파일이 바뀌었어?" --mode hybrid --repeat 2 --save
```

Create the APIM gateway scaffold:

```powershell
$ApimName = "<globally-unique-apim-name>"
$PublisherEmail = "<publisher-email>"

az apim create `
  --name $ApimName `
  --resource-group $ResourceGroup `
  --location $Location `
  --publisher-name "geond" `
  --publisher-email $PublisherEmail `
  --sku-name Consumption

az apim backend create `
  --resource-group $ResourceGroup `
  --service-name $ApimName `
  --backend-id geond-foundry-openai `
  --url $env:GEOND_AZURE_OPENAI_ENDPOINT `
  --protocol http

az apim api create `
  --resource-group $ResourceGroup `
  --service-name $ApimName `
  --api-id geond-openai `
  --path openai `
  --display-name "Geond OpenAI Gateway" `
  --service-url $env:GEOND_AZURE_OPENAI_ENDPOINT `
  --protocols https
```

Create a temporary VM for the multilingual local SLM benchmark:

```powershell
$VmName = "geondslm$RunId"

az vm create `
  --resource-group $ResourceGroup `
  --name $VmName `
  --image Ubuntu2204 `
  --size Standard_B2s `
  --admin-username azureuser `
  --generate-ssh-keys `
  --nsg-rule NONE

az vm run-command invoke `
  --resource-group $ResourceGroup `
  --name $VmName `
  --command-id RunShellScript `
  --scripts "python3 --version"
```

Always clean up:

```powershell
az group delete --name $ResourceGroup --yes --no-wait
az group wait --name $ResourceGroup --deleted
az group list --query "[?starts_with(name, 'rg-geond-validate-')].name" -o json
```

The final query should return `[]` for validation resource groups.

## Azure Portal Path

Use the Portal path when you want to inspect each resource visually or teach the
flow to someone who is not comfortable with Azure CLI yet.

### 1. Select Subscription And Create Resource Group

1. Open <https://portal.azure.com/>.
2. Use the top search bar and open **Resource groups**.
3. Select **Create**.
4. Choose the target subscription.
5. Set **Resource group** to `rg-geond-validate-<date>`.
6. Set **Region** to the validation region, for example `Korea Central`.
7. Add tags such as `project=geond-agent-protocol`, `purpose=validation`, and
   `deleteAfter=<date>`.
8. Select **Review + create**, then **Create**.

AWS mental model: this is the cleanup boundary you might otherwise approximate
with a CloudFormation stack and cost allocation tags. GCP mental model: this is
similar to using a dedicated project or strongly labeled resources.

### 2. Create Azure OpenAI

1. Search for **Azure OpenAI** or **AI Foundry**.
2. Select **Create Azure OpenAI**.
3. Pick the same subscription and resource group.
4. Choose a supported region and the `S0` pricing tier.
5. Give the resource a globally unique name.
6. Create the resource.
7. Open the resource and find **Keys and Endpoint** for API-key testing.
8. Open **Azure AI Foundry** or the model deployment blade.
9. Deploy `text-embedding-3-small`.
10. Use a deployment name such as `text-embedding-small-prod` and set capacity
    high enough for smoke tests. The validated run used capacity `7` because
    capacity `1` hit request-rate limits.

AWS mental model: Bedrock model access plus an invocation endpoint. GCP mental
model: Vertex AI endpoint or publisher-model endpoint.

For production, prefer Entra ID authentication through managed identity rather
than storing account keys in `.env`.

### 3. Create API Management Gateway

1. Search for **API Management services**.
2. Select **Create**.
3. Use the validation resource group and region.
4. Select the **Consumption** tier for a temporary validation gateway.
5. Fill publisher name and email.
6. Wait for APIM provisioning to complete.
7. Open the APIM service and go to **APIs**.
8. Select **Add API** > **HTTP**.
9. Use `geond-openai` as the API display name and `openai` as the path.
10. Set the Web service URL to the Azure OpenAI endpoint.
11. Open **Backends** and add backend entries for model routing experiments.
12. Open **Design** > **All operations** > **Inbound processing** to add policy
    XML if you want to test gateway policy behavior.

AWS mental model: API Gateway routes plus integrations, usage plans, and
authorizers. GCP mental model: Apigee or API Gateway routing/policy layer.

The sample APIM policy is in
[examples/azure/apim/geond-openai-gateway-policy.xml](../examples/azure/apim/geond-openai-gateway-policy.xml).

### 4. Create Temporary VM For Local SLM Benchmark

1. Search for **Virtual machines**.
2. Select **Create** > **Azure virtual machine**.
3. Use the validation resource group.
4. Choose Ubuntu Server and size `Standard_B2s` for the lightweight benchmark.
5. Use SSH key authentication.
6. For a run-command only benchmark, avoid opening inbound ports unless you need
   SSH access.
7. After creation, open the VM and use **Run command** > **RunShellScript**.
8. Install Python dependencies and run the multilingual benchmark script.
9. Save only sanitized metrics, not raw access tokens or ARM IDs.

AWS mental model: EC2 instance with Systems Manager Run Command. GCP mental
model: Compute Engine VM with OS Login or startup scripts.

### 5. Inspect Cost And Delete Everything

1. Open the validation resource group.
2. Review the resource list and tags.
3. Open **Cost Management** > **Cost analysis** for the subscription or resource
   group.
4. Filter by the validation tags if costs have propagated.
5. Export the important signals: SKU, region, duration, capacity, and observed
   runtime.
6. Delete the resource group.
7. Wait until the group disappears from **Resource groups**.

Do not delete individual resources one by one unless debugging a failed cleanup.
The resource group is the safety boundary.

## Cost Signals To Record

The validation artifacts should capture enough information to estimate cost even
when final billing is delayed:

- Region and SKU for each resource.
- VM size, OS, start time, stop/delete time, and observed runtime minutes.
- Azure OpenAI deployment model, SKU, capacity, request counts, and rate limits.
- APIM tier and provisioning duration.
- Tags used for cost filtering.
- Final cleanup status and deletion timestamp.

The 20260512 combined evidence includes a B2s Korea Central Linux price signal
of `0.052 USD/hour`, about `11.8` observed VM runtime minutes, and an estimated
VM compute cost of about `$0.0102` for that short benchmark.

## Production Hardening Checklist

- Move resource definitions to Bicep or Terraform under `infra/` before using a
  long-lived deployment.
- Run `az deployment group what-if` or `azd provision --preview` before applying
  infrastructure changes.
- Use managed identity and RBAC for Azure OpenAI where possible.
- Store secrets in Key Vault, not source files or shell history.
- Enable APIM logging and Azure Monitor diagnostics for gateway experiments.
- Keep provider quotas and regional availability in the deployment notes.
- Use private networking when model traffic or memory data should not traverse
  the public internet.
- Keep `GEOND_PRIVACY_MODE=local-only` for local-only tests and switch to
  `redacted-cloud` only when cloud embedding calls are intentional.