# Azure Team Collaboration Validation

This runbook validates Geond as local-first collaboration infrastructure. The
MCP server, CLI, and dashboard still run on each developer machine. The shared
cloud resource is PostgreSQL-compatible storage, not a centrally hosted MCP
process.

## Current Architecture Assumption

```text
Windows / Codex local geond-mcp  ─┐
MacBook / Claude local geond-mcp ─┼─ Azure Database for PostgreSQL Flexible Server
CI or PM agent local geond CLI   ─┘

Optional embedding path:
local Geond client -> APIM AI gateway -> Azure OpenAI / Foundry deployment
```

APIM is validated as an embedding/model gateway for quota, routing, metrics, and
policy controls. Putting Geond MCP itself behind APIM requires a separate
HTTP/SSE transport and authentication design, so it is out of scope for this
first team-collaboration validation.

## Provisioning Script

Use the script from a Windows machine with `az login` already completed:

```powershell
.\scripts\azure_team_collab_validate.ps1 -Mode Provision -ImportLocalData
```

The script creates:

- one resource group named `rg-geond-team-validate-<run-id>`
- one Azure Database for PostgreSQL Flexible Server, PostgreSQL 16, Burstable
  `Standard_B1ms`
- one `geond` database with `pgcrypto`, `pg_trgm`, and `vector` enabled through
  `azure.extensions`
- one firewall rule for the current public IP
- a local, ignored `connection.local.ps1` file containing the temporary
  connection string
- sanitized evidence under `docs/azure_validation/<run-id>/`

Every cloud resource receives these tags:

- `project=geond-agent-protocol`
- `purpose=team-collab-validation`
- `run_id=<timestamp>`
- `delete_after=<yyyy-mm-dd>`

## Windows Validation Steps

The provisioning script runs the first Windows smoke automatically:

1. set `GEOND_DATABASE_URL` to Azure PostgreSQL
2. run `geond migrate`
3. optionally import local data with `pg_dump`/`psql` when PostgreSQL client
   tools are available
4. run `seed-sample`
5. create a file reservation and symbol reservation as `windows-codex`
6. record a handoff to `macbook-agent`
7. read `dashboard-overview` and `dashboard-events`

Manual follow-up:

```powershell
. .\docs\azure_validation\<run-id>\connection.local.ps1
uv run geond doctor
uv run geond dashboard-overview <workspace-id> --limit 20
uv run geond dashboard-events <workspace-id> --limit 20
uv run geond conflicts <workspace-id>
uv run geond list-handoffs <workspace-id>
uv run geond dashboard serve --host 127.0.0.1 --port 8879
```

## MacBook Apple Silicon Validation

The MacBook should not need any Windows-only state beyond the temporary database
password. Share the password privately, not through git.

```bash
git clone https://github.com/geondongkim/geond-agent-protocol.git
cd geond-agent-protocol
uv sync
export GEOND_DATABASE_URL='postgresql://geondadmin:<password>@<server>.postgres.database.azure.com:5432/geond?sslmode=require'
export GEOND_PRIVACY_MODE=local-only

uv run geond doctor
uv run geond dashboard-overview <workspace-id> --limit 20
uv run geond dashboard-events <workspace-id> --limit 20
uv run geond conflicts <workspace-id>
uv run geond list-handoffs <workspace-id>
uv run geond search --workspace-uri file:///sample/geond --mode keyword app_context
uv run geond benchmark-search --workspace-uri file:///sample/geond --mode keyword --repeat 3 --limit 5 app_context
uv run geond dashboard serve --host 127.0.0.1 --port 8879
```

When keeping both local and Azure URLs in one `.env`, prefer the profile form:

```bash
export GEOND_DATABASE_PROFILE=azure
export AZURE_GEOND_DATABASE_URL='postgresql://geondadmin:<password>@<server>.postgres.database.azure.com:5432/geond?sslmode=require'
```

`GEOND_DATABASE_URL` can remain pointed at local Docker PostgreSQL for normal
offline development.

Expected result:

- MacBook sees Windows-created sessions, messages, reservations, conflicts,
  handoffs, dashboard overview, and events from the same database.
- Keyword search and benchmark work without cloud embedding calls.
- The dashboard opens locally on the MacBook, but reads shared Azure PostgreSQL
  state and shows safe database source metadata.
- The Sessions view reports readable captured prompts separately from raw stored
  messages, so tool-heavy recent windows still expose human conversation context.

## Optional APIM / Embedding Gateway Validation

After shared database behavior is proven, validate model calls separately:

1. create Azure OpenAI or Foundry deployment
2. put APIM in front as the AI gateway
3. configure both Windows and MacBook:

```bash
export GEOND_EMBEDDING_PROVIDER=gateway
export GEOND_EMBEDDING_BASE_URL='<apim-openai-compatible-url>'
export GEOND_EMBEDDING_API_KEY='<apim-subscription-key>'
export GEOND_PRIVACY_MODE=redacted-cloud
```

4. run:

```bash
uv run geond embed-messages --limit 100
uv run geond benchmark-search --workspace-uri file:///sample/geond --mode hybrid --save --repeat 3 app_context
```

APIM policy application should remain an explicit opt-in step because previous
validation showed timeout risk during policy writes.

## Evidence Files

Each run should leave these sanitized artifacts:

- `team_collab_summary.json`
- `cost_ledger.json`
- `windows_client.md`
- `macbook_client.md`
- `cleanup_verification.json` after deletion
- optional GIF showing Windows writes and MacBook reads

Secret-bearing files are ignored:

- `connection.local.ps1`
- SQL dumps
- PostgreSQL dump files

## Cleanup

Delete the whole resource group:

```powershell
az group delete --name rg-geond-team-validate-<run-id> --yes
az group list --query "[?starts_with(name, 'rg-geond-team-validate-')].name" -o table
```

The validation is not complete until no matching temporary resource group remains
or any intentionally retained group is documented with a reason and owner.
