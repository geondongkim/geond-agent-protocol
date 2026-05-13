param(
    [ValidateSet("Provision", "Cleanup")]
    [string]$Mode = "Provision",
    [string]$RunId = (Get-Date -Format "yyyyMMdd-HHmmss"),
    [string]$Location = "koreacentral",
    [string]$ResourceGroup = "",
    [string]$ServerName = "",
    [string]$DatabaseName = "geond",
    [string]$AdminUser = "geondadmin",
    [securestring]$AdminPassword,
    [switch]$ImportLocalData,
    [string]$LocalDatabaseUrl = $env:GEOND_DATABASE_URL,
    [switch]$SkipCleanup
)

$ErrorActionPreference = "Stop"

function ConvertTo-PlainText([securestring]$Secret) {
    return [System.Net.NetworkCredential]::new("", $Secret).Password
}

function New-Password {
    $bytes = New-Object byte[] 18
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    } finally {
        $rng.Dispose()
    }
    $raw = [Convert]::ToBase64String($bytes).TrimEnd("=")
    return "Gd!" + $raw.Replace("+", "A").Replace("/", "z")
}

function Write-JsonFile($Path, $Value) {
    $Value | ConvertTo-Json -Depth 12 | Set-Content -Path $Path -Encoding UTF8
}

function Invoke-NativeCommand {
    param(
        [Parameter(Mandatory = $true)]
        [scriptblock]$Command,
        [Parameter(Mandatory = $true)]
        [string]$Description
    )
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE"
    }
}

$runDir = Join-Path "docs/azure_validation" $RunId
New-Item -ItemType Directory -Force -Path $runDir | Out-Null

if (-not $ResourceGroup) {
    $ResourceGroup = "rg-geond-team-validate-$RunId"
}
if (-not $ServerName) {
    $suffix = ($RunId.ToLowerInvariant() -replace "[^a-z0-9]", "")
    $ServerName = "pg-geond-team-$suffix"
    if ($ServerName.Length -gt 63) {
        $ServerName = $ServerName.Substring(0, 63)
    }
}

if ($Mode -eq "Cleanup") {
    az group delete --name $ResourceGroup --yes
    $remaining = az group list --query "[?starts_with(name, 'rg-geond-team-validate-')].name" -o json |
        ConvertFrom-Json
    Write-JsonFile (Join-Path $runDir "cleanup_verification.json") @{
        mode = $Mode
        resource_group = $ResourceGroup
        deleted_at = (Get-Date).ToString("o")
        remaining_validation_resource_groups = $remaining
    }
    return
}

if (-not $AdminPassword) {
    $AdminPassword = ConvertTo-SecureString (New-Password) -AsPlainText -Force
}
$plainPassword = ConvertTo-PlainText $AdminPassword
$escapedPassword = [uri]::EscapeDataString($plainPassword)
$serverFqdn = "$ServerName.postgres.database.azure.com"
$databaseUrl = "postgresql://${AdminUser}:${escapedPassword}@${serverFqdn}:5432/${DatabaseName}?sslmode=require"
$redactedUrl = "postgresql://${AdminUser}:***@${serverFqdn}:5432/${DatabaseName}?sslmode=require"

$account = az account show | ConvertFrom-Json
$clientIp = (Invoke-RestMethod "https://api.ipify.org").Trim()
$tags = @(
    "project=geond-agent-protocol",
    "purpose=team-collab-validation",
    "run_id=$RunId",
    "delete_after=$((Get-Date).AddDays(1).ToString('yyyy-MM-dd'))"
)

Invoke-NativeCommand { az group create --name $ResourceGroup --location $Location --tags $tags | Out-Null } `
    "Create resource group"
Invoke-NativeCommand {
    az postgres flexible-server create `
        --resource-group $ResourceGroup `
        --name $ServerName `
        --location $Location `
        --version 16 `
        --tier Burstable `
        --sku-name Standard_B1ms `
        --storage-size 32 `
        --admin-user $AdminUser `
        --admin-password $plainPassword `
        --public-access $clientIp `
        --yes | Out-Null
} "Create PostgreSQL flexible server"
Invoke-NativeCommand {
    az postgres flexible-server db create `
        --resource-group $ResourceGroup `
        --server-name $ServerName `
        --database-name $DatabaseName | Out-Null
} "Create PostgreSQL database"
Invoke-NativeCommand {
    az postgres flexible-server firewall-rule create `
        --resource-group $ResourceGroup `
        --name $ServerName `
        --rule-name current-client `
        --start-ip-address $clientIp `
        --end-ip-address $clientIp | Out-Null
} "Create PostgreSQL firewall rule"
Invoke-NativeCommand {
    az postgres flexible-server parameter set `
        --resource-group $ResourceGroup `
        --server-name $ServerName `
        --name azure.extensions `
        --value "pgcrypto,pg_trgm,vector" | Out-Null
} "Enable extension allow-list"
Invoke-NativeCommand {
    az postgres flexible-server restart --resource-group $ResourceGroup --name $ServerName | Out-Null
} "Restart PostgreSQL flexible server"

Set-Content -Path (Join-Path $runDir "connection.local.ps1") -Encoding UTF8 -Value @"
`$env:GEOND_DATABASE_URL = "$databaseUrl"
`$env:GEOND_PRIVACY_MODE = "local-only"
"@

$env:GEOND_DATABASE_URL = $databaseUrl
uv run geond migrate
if ($LASTEXITCODE -ne 0) { throw "geond migrate failed with exit code $LASTEXITCODE" }

$import = @{
    requested = [bool]$ImportLocalData
    attempted = $false
    status = "skipped"
}

if ($ImportLocalData) {
    $import.attempted = $true
    $pgDump = Get-Command pg_dump -ErrorAction SilentlyContinue
    $psql = Get-Command psql -ErrorAction SilentlyContinue
    if (-not $LocalDatabaseUrl) {
        $import.status = "skipped_no_local_database_url"
    } elseif (-not $pgDump -or -not $psql) {
        $import.status = "skipped_pg_tools_missing"
    } else {
        $dumpPath = Join-Path $runDir "geond_local_data.sql"
        & $pgDump.Source --data-only --no-owner --no-acl --file $dumpPath $LocalDatabaseUrl
        & $psql.Source $databaseUrl --file $dumpPath
        $import.status = "imported_data_only"
    }
}

$seed = uv run geond seed-sample | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw "geond seed-sample failed with exit code $LASTEXITCODE" }
$workspaceId = $seed.workspace_id
uv run geond reserve-files $workspaceId `
    --agent-name windows-codex `
    --file docs/agent_activity_dashboard.md `
    --purpose "Azure shared DB validation" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "geond reserve-files failed with exit code $LASTEXITCODE" }
uv run geond reserve-symbols $workspaceId `
    --agent-name windows-codex `
    --symbol geond.dashboard.sessions `
    --purpose "Azure shared DB validation" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "geond reserve-symbols failed with exit code $LASTEXITCODE" }
uv run geond record-handoff $workspaceId `
    --from-agent windows-codex `
    --to-agent macbook-agent `
    --summary "Windows client wrote this handoff into shared Azure PostgreSQL." `
    --next-action "MacBook should list handoffs, conflicts, search memory, and inspect symbol context." | Out-Null
if ($LASTEXITCODE -ne 0) { throw "geond record-handoff failed with exit code $LASTEXITCODE" }
$overview = uv run geond dashboard-overview $workspaceId --limit 10 | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw "geond dashboard-overview failed with exit code $LASTEXITCODE" }
$events = uv run geond dashboard-events $workspaceId --limit 10 | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw "geond dashboard-events failed with exit code $LASTEXITCODE" }

Write-JsonFile (Join-Path $runDir "team_collab_summary.json") @{
    run_id = $RunId
    created_at = (Get-Date).ToString("o")
    subscription_name = $account.name
    location = $Location
    resource_group = $ResourceGroup
    postgres_server = $ServerName
    postgres_fqdn = $serverFqdn
    database = $DatabaseName
    admin_user = $AdminUser
    redacted_database_url = $redactedUrl
    client_ip_firewall_rule = $clientIp
    import = $import
    seed_workspace_id = $workspaceId
    overview_counts = $overview.counts
    event_count = $events.events.Count
    cleanup_command = "az group delete --name $ResourceGroup --yes"
}

Write-JsonFile (Join-Path $runDir "cost_ledger.json") @{
    run_id = $RunId
    resources = @(
        @{
            type = "Microsoft.DBforPostgreSQL/flexibleServers"
            sku = "Burstable Standard_B1ms"
            storage_gb = 32
            location = $Location
            started_at = (Get-Date).ToString("o")
            notes = "Stop/delete the resource group immediately after validation."
        }
    )
    tags = @{
        project = "geond-agent-protocol"
        purpose = "team-collab-validation"
        run_id = $RunId
        delete_after = (Get-Date).AddDays(1).ToString("yyyy-MM-dd")
    }
}

Set-Content -Path (Join-Path $runDir "windows_client.md") -Encoding UTF8 -Value @"
# Windows Client Validation

Run id: `$RunId`

1. Source the local connection file that was intentionally not committed:

~~~powershell
. .\docs\azure_validation\$RunId\connection.local.ps1
~~~

2. Run:

~~~powershell
uv run geond doctor
uv run geond dashboard-overview $workspaceId --limit 10
uv run geond dashboard-events $workspaceId --limit 10
uv run geond conflicts $workspaceId
uv run geond list-handoffs $workspaceId
uv run geond dashboard serve --host 127.0.0.1 --port 8879
~~~
"@

Set-Content -Path (Join-Path $runDir "macbook_client.md") -Encoding UTF8 -Value @"
# MacBook Apple Silicon Client Validation

Run id: `$RunId`

Use the redacted URL from team_collab_summary.json and ask the Windows owner
for the temporary password through a private channel. Do not commit the password.

~~~bash
git clone https://github.com/geondongkim/geond-agent-protocol.git
cd geond-agent-protocol
uv sync
export GEOND_DATABASE_URL='postgresql://${AdminUser}:<password>@${serverFqdn}:5432/${DatabaseName}?sslmode=require'
export GEOND_PRIVACY_MODE=local-only

uv run geond doctor
uv run geond dashboard-overview $workspaceId --limit 20
uv run geond dashboard-events $workspaceId --limit 20
uv run geond conflicts $workspaceId
uv run geond list-handoffs $workspaceId
uv run geond search --workspace-uri file:///sample/geond --mode keyword app_context
uv run geond benchmark-search --workspace-uri file:///sample/geond --mode keyword --repeat 3 --limit 5 app_context
uv run geond dashboard serve --host 127.0.0.1 --port 8879
~~~

Expected result: the MacBook sees the Windows-created sessions, messages,
reservations, conflicts, handoff, dashboard overview, and benchmark/search
behavior from the same shared Azure PostgreSQL database.
"@

if (-not $SkipCleanup) {
    Write-Host "Provisioned $ResourceGroup. Cleanup manually with:"
    Write-Host "az group delete --name $ResourceGroup --yes"
}
