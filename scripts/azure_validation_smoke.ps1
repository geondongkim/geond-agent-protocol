param(
    [string]$Location = "koreacentral",
    [switch]$SkipApim,
    [switch]$SkipVm,
    [switch]$SkipAzureOpenAI,
    [switch]$ApplyApimPolicy
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [Console]::OutputEncoding
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$RunId = Get-Date -Format "yyyyMMdd-HHmmss"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ResultDir = Join-Path $RepoRoot "docs/azure_validation/$RunId"
New-Item -ItemType Directory -Force $ResultDir | Out-Null

$env:AZURE_EXTENSION_DIR = Join-Path $env:TEMP "geond-azext-$RunId"
New-Item -ItemType Directory -Force $env:AZURE_EXTENSION_DIR | Out-Null

$SafeRun = $RunId.Replace("-", "")
$Suffix = (Get-Random -Minimum 10000 -Maximum 99999).ToString()
$ResourceGroup = "rg-geond-validate-$SafeRun"
$OpenAiName = "geondaoai$Suffix"
$DeploymentName = "text-embedding-3-small"
$ApimName = "geondapim$Suffix"
$VmName = "geond-vm-$Suffix"
$AdminUser = "azureuser"
$Tags = @("project=geond-agent-protocol", "purpose=validation", "runId=$RunId", "deleteAfter=immediate")

$Summary = [ordered]@{
    run_id = $RunId
    started_at = (Get-Date).ToUniversalTime().ToString("o")
    location = $Location
    resource_group = $ResourceGroup
    resources = @()
    steps = @()
    cost_inputs = [ordered]@{
        vm_sku = "Standard_B2s"
        vm_linux_retail_usd_per_hour_koreacentral = 0.052
        azure_openai_sku = "S0"
        azure_openai_deployment_sku = "GlobalStandard"
        azure_openai_deployment_capacity = 7
        apim_sku = "Consumption"
        note = "Azure OpenAI and APIM charges are usage based; VM runtime is recorded for later cost calculations."
    }
    cleanup = [ordered]@{
        attempted = $false
        status = "pending"
    }
}

function Save-Json($Name, $Value) {
    $Path = Join-Path $ResultDir $Name
    $Value | ConvertTo-Json -Depth 40 | Out-File -FilePath $Path -Encoding utf8
}

function Get-ShortSha256([string]$Value) {
    $Sha = [System.Security.Cryptography.SHA256]::Create()
    $Bytes = [System.Text.Encoding]::UTF8.GetBytes($Value)
    $Hash = $Sha.ComputeHash($Bytes)
    return ([System.BitConverter]::ToString($Hash).Replace("-", "").ToLowerInvariant()).Substring(0, 12)
}

function Add-Step($Name, $Status, $Details = $null, $StartedAt = $null) {
    $Step = [ordered]@{
        name = $Name
        status = $Status
        timestamp = (Get-Date).ToUniversalTime().ToString("o")
    }
    if ($StartedAt) {
        $Step.duration_seconds = [Math]::Round(((Get-Date) - $StartedAt).TotalSeconds, 3)
    }
    if ($null -ne $Details) {
        $Step.details = $Details
    }
    $Summary.steps += $Step
    Save-Json "summary.json" $Summary
}

function Add-Resource($Type, $Name, $Sku = $null) {
    $Resource = [ordered]@{
        type = $Type
        name = $Name
        location = $Location
    }
    if ($Sku) {
        $Resource.sku = $Sku
    }
    $Summary.resources += $Resource
    Save-Json "summary.json" $Summary
}

function Invoke-AzJson([string[]]$AzArgs) {
    $Output = & az @AzArgs -o json
    if ($LASTEXITCODE -ne 0) {
        throw "az $($AzArgs -join ' ') failed"
    }
    return $Output | ConvertFrom-Json
}

function Invoke-AzTsv([string[]]$AzArgs) {
    $Output = & az @AzArgs -o tsv
    if ($LASTEXITCODE -ne 0) {
        throw "az $($AzArgs -join ' ') failed"
    }
    return ($Output | Out-String).Trim()
}

function ConvertTo-PublicDetails($Value) {
    if ($null -eq $Value) {
        return $null
    }
    if ($Value -is [hashtable] -or $Value -is [System.Collections.Specialized.OrderedDictionary]) {
        return $Value
    }
    if ($Value -is [array]) {
        return @{ count = $Value.Count }
    }
    $Properties = $Value.PSObject.Properties
    if ($Properties["name"] -or $Properties["type"] -or $Properties["sku"] -or $Properties["properties"]) {
        $Public = [ordered]@{}
        if ($Properties["name"]) { $Public.name = $Value.name }
        if ($Properties["type"]) { $Public.type = $Value.type }
        if ($Properties["location"]) { $Public.location = $Value.location }
        if ($Value.sku) {
            $Public.sku = [ordered]@{
                name = $Value.sku.name
                capacity = $Value.sku.capacity
            }
        }
        if ($Value.properties) {
            if ($Value.properties.provisioningState) { $Public.provisioning_state = $Value.properties.provisioningState }
            if ($Value.properties.model) {
                $Public.model = [ordered]@{
                    format = $Value.properties.model.format
                    name = $Value.properties.model.name
                    version = $Value.properties.model.version
                }
            }
            if ($Value.properties.rateLimits) { $Public.rate_limits = $Value.properties.rateLimits }
        }
        return $Public
    }
    return $Value
}

function Invoke-ExternalText([string]$Name, [scriptblock]$Block) {
    $PreviousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $Output = & $Block 2>&1
        $ExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }
    $Text = ($Output | Out-String).Trim()
    if ($ExitCode -ne 0) {
        throw "$Name failed with exit code $ExitCode`n$Text"
    }
    return $Text
}

function Invoke-ValidationStep($Name, [scriptblock]$Block) {
    $StartedAt = Get-Date
    try {
        $Result = & $Block
        Add-Step $Name "ok" (ConvertTo-PublicDetails $Result) $StartedAt
        return $Result
    }
    catch {
        Add-Step $Name "failed" @{ error = $_.Exception.Message } $StartedAt
        return $null
    }
}

try {
    $Account = Invoke-AzJson @("account", "show")
    $Summary.subscription = [ordered]@{
        tenant_id_hash = Get-ShortSha256 $Account.tenantId
        subscription_id_hash = Get-ShortSha256 $Account.id
    }
    Save-Json "summary.json" $Summary

    $GroupStartedAt = Get-Date
    Invoke-AzJson (@("group", "create", "--name", $ResourceGroup, "--location", $Location, "--tags") + $Tags) | Out-Null
    Add-Resource "Microsoft.Resources/resourceGroups" $ResourceGroup
    Add-Step "create-resource-group" "ok" @{ name = $ResourceGroup } $GroupStartedAt

    if (-not $SkipAzureOpenAI) {
        $OpenAiStartedAt = Get-Date
        Invoke-AzJson (@(
            "cognitiveservices", "account", "create",
            "--name", $OpenAiName,
            "--resource-group", $ResourceGroup,
            "--location", $Location,
            "--kind", "OpenAI",
            "--sku", "S0",
            "--custom-domain", $OpenAiName,
            "--assign-identity",
            "--yes",
            "--tags"
        ) + $Tags) | Out-Null
        Add-Resource "Microsoft.CognitiveServices/accounts" $OpenAiName "S0"
        Add-Step "create-azure-openai-account" "ok" @{ name = $OpenAiName } $OpenAiStartedAt

        $DeploymentStartedAt = Get-Date
        $DeploymentResult = Invoke-ValidationStep "create-embedding-deployment-globalstandard" {
            Invoke-AzJson @(
                "cognitiveservices", "account", "deployment", "create",
                "--resource-group", $ResourceGroup,
                "--name", $OpenAiName,
                "--deployment-name", $DeploymentName,
                "--model-format", "OpenAI",
                "--model-name", "text-embedding-3-small",
                "--model-version", "1",
                "--sku-name", "GlobalStandard",
                "--sku-capacity", "7"
            )
        }
        if ($null -eq $DeploymentResult) {
            Invoke-AzJson @(
                "cognitiveservices", "account", "deployment", "create",
                "--resource-group", $ResourceGroup,
                "--name", $OpenAiName,
                "--deployment-name", $DeploymentName,
                "--model-format", "OpenAI",
                "--model-name", "text-embedding-3-small",
                "--model-version", "1",
                "--sku-name", "Standard",
                "--sku-capacity", "7"
            ) | Out-Null
            Add-Step "create-embedding-deployment-standard" "ok" @{ deployment = $DeploymentName } $DeploymentStartedAt
            $Summary.cost_inputs.azure_openai_deployment_sku = "Standard"
        }
        Add-Resource "Microsoft.CognitiveServices/accounts/deployments" "$OpenAiName/$DeploymentName" $Summary.cost_inputs.azure_openai_deployment_sku

        $Endpoint = Invoke-AzTsv @("cognitiveservices", "account", "show", "--resource-group", $ResourceGroup, "--name", $OpenAiName, "--query", "properties.endpoint")
        $Key = Invoke-AzTsv @("cognitiveservices", "account", "keys", "list", "--resource-group", $ResourceGroup, "--name", $OpenAiName, "--query", "key1")

        $LocalStartedAt = Get-Date
        Push-Location $RepoRoot
        try {
            Invoke-ExternalText "docker compose up postgres" { docker compose up -d postgres } | Out-Null
            Invoke-ExternalText "docker migrate" { docker compose --profile tools run --rm geond-migrate } | Out-File -FilePath (Join-Path $ResultDir "docker_migration.txt") -Encoding utf8
            Invoke-ExternalText "purge sample before benchmark" { uv run geond purge-workspace "file:///sample/geond" --yes } | Out-Null
            $Seed = (Invoke-ExternalText "seed sample" { uv run geond seed-sample }) | ConvertFrom-Json
            $env:GEOND_EMBEDDING_PROVIDER = "azure-openai"
            $env:GEOND_AZURE_OPENAI_ENDPOINT = $Endpoint
            $env:GEOND_AZURE_OPENAI_API_KEY = $Key
            $env:GEOND_AZURE_OPENAI_API_VERSION = "2024-10-21"
            $env:GEOND_AZURE_OPENAI_EMBEDDING_DEPLOYMENT = $DeploymentName
            $env:GEOND_EMBEDDING_DIMENSIONS = "1536"
            $EmbedOutput = (Invoke-ExternalText "embed messages with Azure OpenAI" { uv run geond embed-messages --limit 10 }) | ConvertFrom-Json
            $BenchmarkMarkdown = Invoke-ExternalText "benchmark search with Azure OpenAI" { uv run geond benchmark-search app_context "왜 service.py 파일이 바뀌었어?" --mode hybrid --repeat 2 --workspace-uri "file:///sample/geond" --save --label "azure-openai-$RunId" --format markdown }
            $BenchmarkMarkdown | Out-File -FilePath (Join-Path $ResultDir "azure_openai_benchmark.md") -Encoding utf8
            Invoke-ExternalText "purge sample after benchmark" { uv run geond purge-workspace "file:///sample/geond" --yes } | Out-File -FilePath (Join-Path $ResultDir "local_purge_after_azure_openai.txt") -Encoding utf8
            Add-Step "azure-openai-geond-benchmark" "ok" @{
                workspace_id = $Seed.workspace_id
                embedded = $EmbedOutput.embedded
                benchmark_file = "azure_openai_benchmark.md"
            } $LocalStartedAt
        }
        finally {
            Remove-Item Env:GEOND_EMBEDDING_PROVIDER -ErrorAction SilentlyContinue
            Remove-Item Env:GEOND_AZURE_OPENAI_ENDPOINT -ErrorAction SilentlyContinue
            Remove-Item Env:GEOND_AZURE_OPENAI_API_KEY -ErrorAction SilentlyContinue
            Remove-Item Env:GEOND_AZURE_OPENAI_API_VERSION -ErrorAction SilentlyContinue
            Remove-Item Env:GEOND_AZURE_OPENAI_EMBEDDING_DEPLOYMENT -ErrorAction SilentlyContinue
            Remove-Item Env:GEOND_EMBEDDING_DIMENSIONS -ErrorAction SilentlyContinue
            Pop-Location
        }
    }

    if (-not $SkipApim -and -not $SkipAzureOpenAI) {
        $ApimStartedAt = Get-Date
        $ApimCreated = Invoke-ValidationStep "create-apim-consumption" {
            Invoke-AzJson (@(
                "apim", "create",
                "--name", $ApimName,
                "--resource-group", $ResourceGroup,
                "--location", $Location,
                "--publisher-email", "geond-validation@example.com",
                "--publisher-name", "Geond Validation",
                "--sku-name", "Consumption",
                "--enable-managed-identity",
                "--tags"
            ) + $Tags)
        }
        if ($null -ne $ApimCreated) {
            Add-Resource "Microsoft.ApiManagement/service" $ApimName "Consumption"
            $OpenAiEndpoint = Invoke-AzTsv @("cognitiveservices", "account", "show", "--resource-group", $ResourceGroup, "--name", $OpenAiName, "--query", "properties.endpoint")
            Invoke-ValidationStep "create-apim-backends" {
                Invoke-AzJson @("apim", "backend", "create", "--resource-group", $ResourceGroup, "--service-name", $ApimName, "--backend-id", "geond-foundry-openai", "--url", $OpenAiEndpoint, "--protocol", "http") | Out-Null
                Invoke-AzJson @("apim", "backend", "create", "--resource-group", $ResourceGroup, "--service-name", $ApimName, "--backend-id", "geond-embeddings", "--url", $OpenAiEndpoint, "--protocol", "http") | Out-Null
                Invoke-AzJson @("apim", "backend", "create", "--resource-group", $ResourceGroup, "--service-name", $ApimName, "--backend-id", "geond-content-safety", "--url", $OpenAiEndpoint, "--protocol", "http") | Out-Null
                @{ backend_count = 3 }
            } | Out-Null
            Invoke-ValidationStep "create-apim-api" {
                Invoke-AzJson @(
                    "apim", "api", "create",
                    "--resource-group", $ResourceGroup,
                    "--service-name", $ApimName,
                    "--api-id", "geond-openai",
                    "--path", "openai",
                    "--display-name", "Geond OpenAI Gateway",
                    "--protocols", "https",
                    "--service-url", $OpenAiEndpoint,
                    "--subscription-required", "false"
                )
            } | Out-Null
            if ($ApplyApimPolicy) {
                Invoke-ValidationStep "apply-apim-ai-gateway-policy" {
                    $PolicyXml = Get-Content -Raw (Join-Path $RepoRoot "examples/azure/apim/geond-openai-gateway-policy.xml")
                    $PolicyBodyPath = Join-Path $ResultDir "apim_policy_body.json"
                    @{ properties = @{ format = "rawxml"; value = $PolicyXml } } | ConvertTo-Json -Depth 10 | Out-File -FilePath $PolicyBodyPath -Encoding utf8
                    $SubId = $Account.id
                    $PolicyUri = "https://management.azure.com/subscriptions/$SubId/resourceGroups/$ResourceGroup/providers/Microsoft.ApiManagement/service/$ApimName/apis/geond-openai/policies/policy?api-version=2022-08-01"
                    Invoke-AzJson @("rest", "--method", "put", "--url", $PolicyUri, "--body", "@$PolicyBodyPath")
                } | Out-Null
            }
            else {
                Add-Step "apply-apim-ai-gateway-policy" "skipped" @{ reason = "Use -ApplyApimPolicy to opt into the long-running APIM policy REST smoke." }
            }
            Add-Step "apim-consumption-validation" "ok" @{ name = $ApimName } $ApimStartedAt
        }
    }

    if (-not $SkipVm) {
        $VmStartedAt = Get-Date
        Invoke-AzJson (@(
            "vm", "create",
            "--resource-group", $ResourceGroup,
            "--name", $VmName,
            "--location", $Location,
            "--image", "Ubuntu2204",
            "--size", "Standard_B2s",
            "--admin-username", $AdminUser,
            "--generate-ssh-keys",
            "--storage-sku", "Standard_LRS",
            "--nsg-rule", "NONE",
            "--tags"
        ) + $Tags) | Out-Null
        Add-Resource "Microsoft.Compute/virtualMachines" $VmName "Standard_B2s"
        Add-Step "create-b2s-vm" "ok" @{ name = $VmName } $VmStartedAt

        $VmScript = Join-Path $ResultDir "vm_slm_benchmark.sh"
        @'
set -e
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -y >/dev/null
sudo apt-get install -y python3-venv python3-pip >/dev/null
python3 -m venv /tmp/geond-slm
/tmp/geond-slm/bin/pip install -q --upgrade pip
/tmp/geond-slm/bin/pip install -q sentence-transformers scikit-learn
cat >/tmp/geond_slm_benchmark.py <<'PY'
from __future__ import annotations

import json
import os
import platform
import time
from base64 import b64encode

import numpy as np
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DOCS = [
    "service.py was changed to keep database initialization inside app_context.",
    "Flask 애플리케이션 컨텍스트 안에서 데이터베이스 초기화를 수행해야 합니다.",
    "Claude Code importer stores cwd, git branch, uuid, parentUuid, and tool calls.",
    "tree-sitter indexes Python, TypeScript, and JavaScript symbols.",
    "Azure OpenAI gateway benchmarks compare multilingual embedding retrieval.",
]
QUERIES = [
    {"query": "왜 service.py 파일이 바뀌었어?", "expected": 1},
    {"query": "How does Claude Code record tool calls?", "expected": 2},
    {"query": "TypeScript 심볼 인덱싱은 무엇으로 하나요?", "expected": 3},
]

started = time.perf_counter()
model = SentenceTransformer(MODEL)
load_seconds = time.perf_counter() - started

encode_started = time.perf_counter()
doc_vectors = model.encode(DOCS, normalize_embeddings=True)
query_vectors = model.encode([item["query"] for item in QUERIES], normalize_embeddings=True)
encode_seconds = time.perf_counter() - encode_started
scores = cosine_similarity(query_vectors, doc_vectors)

rows = []
reciprocal_ranks = []
for item, row in zip(QUERIES, scores):
    ranking = list(np.argsort(row)[::-1])
    rank = ranking.index(item["expected"]) + 1
    reciprocal_ranks.append(1.0 / rank)
    rows.append(
        {
            "query": item["query"],
            "expected_doc_index": item["expected"],
            "rank": rank,
            "top_doc_index": int(ranking[0]),
            "top_score": round(float(row[ranking[0]]), 6),
            "expected_score": round(float(row[item["expected"]]), 6),
        }
    )

mem_total_kb = None
try:
    with open("/proc/meminfo", "r", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("MemTotal:"):
                mem_total_kb = int(line.split()[1])
                break
except OSError:
    pass

result = {
    "model": MODEL,
    "platform": platform.platform(),
    "python_version": platform.python_version(),
    "cpu_count": os.cpu_count(),
    "mem_total_kb": mem_total_kb,
    "doc_count": len(DOCS),
    "query_count": len(QUERIES),
    "load_seconds": round(load_seconds, 3),
    "encode_seconds": round(encode_seconds, 3),
    "mrr": round(sum(reciprocal_ranks) / len(reciprocal_ranks), 4),
    "rows": rows,
}
payload = json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8")
print("GEOND_SLM_RESULT_B64_BEGIN")
print(b64encode(payload).decode("ascii"))
print("GEOND_SLM_RESULT_B64_END")
PY
/tmp/geond-slm/bin/python /tmp/geond_slm_benchmark.py
'@ | Out-File -FilePath $VmScript -Encoding utf8

        $RunStartedAt = Get-Date
        $RawVmOutput = az vm run-command invoke --resource-group $ResourceGroup --name $VmName --command-id RunShellScript --scripts "@$VmScript" --query "value[0].message" -o tsv
        $RawVmText = ($RawVmOutput | Out-String)
        $RawVmText | Out-File -FilePath (Join-Path $ResultDir "slm_vm_raw.txt") -Encoding utf8
        $BeginMarker = "GEOND_SLM_RESULT_B64_BEGIN"
        $EndMarker = "GEOND_SLM_RESULT_B64_END"
        $Begin = $RawVmText.IndexOf($BeginMarker)
        $End = $RawVmText.IndexOf($EndMarker)
        if ($Begin -ge 0 -and $End -gt $Begin) {
            $PayloadStart = $Begin + $BeginMarker.Length
            $Payload = $RawVmText.Substring($PayloadStart, $End - $PayloadStart).Trim()
            $Json = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($Payload))
            $Json | Out-File -FilePath (Join-Path $ResultDir "slm_vm_benchmark.json") -Encoding utf8
            Add-Step "vm-slm-multilingual-benchmark" "ok" (($Json | ConvertFrom-Json)) $RunStartedAt
        }
        else {
            Add-Step "vm-slm-multilingual-benchmark" "failed" @{ error = "Result markers not found" } $RunStartedAt
        }
    }
}
catch {
    $Summary.error = [ordered]@{
        message = $_.Exception.Message
        category = $_.CategoryInfo.Category
        position = $_.InvocationInfo.PositionMessage
    }
    Add-Step "top-level-error" "failed" $Summary.error
}
finally {
    $Summary.finished_at = (Get-Date).ToUniversalTime().ToString("o")
    $Summary.cleanup.attempted = $true
    $CleanupStartedAt = Get-Date
    try {
        az group delete --name $ResourceGroup --yes --no-wait | Out-Null
        az group wait --name $ResourceGroup --deleted
        $Summary.cleanup.status = "deleted"
        $Summary.cleanup.duration_seconds = [Math]::Round(((Get-Date) - $CleanupStartedAt).TotalSeconds, 3)
    }
    catch {
        $Summary.cleanup.status = "failed"
        $Summary.cleanup.error = $_.Exception.Message
    }
    Save-Json "summary.json" $Summary

    $Markdown = @()
    $Markdown += "# Azure Validation Run $RunId"
    $Markdown += ""
    $Markdown += "- Location: ``$Location``"
    $Markdown += "- Resource group: ``$ResourceGroup``"
    $Markdown += "- Cleanup: ``$($Summary.cleanup.status)``"
    $Markdown += "- VM price signal: Standard_B2s Linux Korea Central ``0.052 USD/hour``"
    $Markdown += ""
    $Markdown += "## Steps"
    $Markdown += ""
    foreach ($Step in $Summary.steps) {
        $Duration = if ($Step.duration_seconds) { " ($($Step.duration_seconds)s)" } else { "" }
        $Markdown += "- $($Step.status): $($Step.name)$Duration"
    }
    $Markdown += ""
    $Markdown += "## Artifacts"
    $Markdown += ""
    $Markdown += "- [summary.json](summary.json)"
    if (Test-Path (Join-Path $ResultDir "azure_openai_benchmark.md")) { $Markdown += "- [azure_openai_benchmark.md](azure_openai_benchmark.md)" }
    if (Test-Path (Join-Path $ResultDir "slm_vm_benchmark.json")) { $Markdown += "- [slm_vm_benchmark.json](slm_vm_benchmark.json)" }
    $Markdown -join "`n" | Out-File -FilePath (Join-Path $ResultDir "README.md") -Encoding utf8

    Write-Host "Azure validation results: $ResultDir"
    Write-Host "Cleanup status: $($Summary.cleanup.status)"
}
