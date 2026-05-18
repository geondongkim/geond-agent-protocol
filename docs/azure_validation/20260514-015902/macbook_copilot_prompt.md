# MacBook GitHub Copilot Chat Prompt

Paste this into GitHub Copilot Chat on the MacBook after the temporary Azure PostgreSQL password is shared privately. Do not paste the password into git-tracked files.

```text
You are GitHub Copilot working in VS Code on my MacBook. Validate the Geond shared Azure PostgreSQL team-collaboration run end to end.

Context:
- Repository: https://github.com/geondongkim/geond-agent-protocol.git
- Run id: 20260514-015902
- Azure PostgreSQL server: pg-geond-team-20260514015902.postgres.database.azure.com
- Database: geond
- Admin user: geondadmin
- Seed workspace id: 7b786436-73e2-4d23-a4a4-f724eaf96c3d
- Imported project workspace id: be7a3558-adda-4c2a-adf1-2c4181ef1b2b
- Expected latest changeset: 54042b2 Split dashboard lanes and add Azure team validation
- Resource group to delete after validation, only when I explicitly approve cleanup: rg-geond-team-validate-20260514-015902

Please do the following without committing secrets:

1. Clone or update the repository, then run `uv sync`.
2. Set environment variables in the terminal only:
   `GEOND_DATABASE_URL=postgresql://geondadmin:<password>@pg-geond-team-20260514015902.postgres.database.azure.com:5432/geond?sslmode=require`
   `GEOND_PRIVACY_MODE=local-only`
   `GEOND_EMBEDDING_PROVIDER=none`
3. Run and summarize these commands:
   `uv run geond doctor --format json`
   `uv run geond dashboard-overview 7b786436-73e2-4d23-a4a4-f724eaf96c3d --limit 20`
   `uv run geond dashboard-events 7b786436-73e2-4d23-a4a4-f724eaf96c3d --limit 20`
   `uv run geond dashboard-overview be7a3558-adda-4c2a-adf1-2c4181ef1b2b --limit 20`
   `uv run geond dashboard-events be7a3558-adda-4c2a-adf1-2c4181ef1b2b --limit 20`
   `uv run geond summarize-changeset 54042b2`
   `uv run geond conflicts 7b786436-73e2-4d23-a4a4-f724eaf96c3d`
   `uv run geond list-handoffs 7b786436-73e2-4d23-a4a4-f724eaf96c3d`
   `uv run geond search --workspace-uri file:///sample/geond --mode keyword app_context`
   `uv run geond search --workspace-uri file:///C:/Users/EL035/dataschool/geond-agent-protocol --mode keyword dashboard`
   `uv run geond mcp-smoke --format json --workspace-uri file:///C:/Users/EL035/dataschool/geond-agent-protocol --query dashboard --limit 3 --allow-empty-search`
4. Start the local dashboard on the MacBook:
   `uv run geond dashboard serve --host 127.0.0.1 --port 8879`
5. Open or query these URLs and verify the tabs, horizontal agent lanes, sessions, recent messages, handoffs, and events load from Azure PostgreSQL:
   `http://127.0.0.1:8879/?workspace=7b786436-73e2-4d23-a4a4-f724eaf96c3d&limit=50`
   `http://127.0.0.1:8879/?workspace=be7a3558-adda-4c2a-adf1-2c4181ef1b2b&limit=50`
   `http://127.0.0.1:8879/api/workspaces/be7a3558-adda-4c2a-adf1-2c4181ef1b2b/sessions?limit=5&message_limit=3`
6. Report whether the MacBook sees:
   - Windows-created Azure seed handoff and `windows-codex` activity
   - Imported project workspace sessions and recent messages
   - `54042b2` changeset evidence
   - MCP smoke success with registered tools/resources
   - Dashboard sessions endpoint response

Do not run Azure cleanup yet. After I confirm MacBook validation is complete, remind me to delete the temporary resource group with:
`az group delete --name rg-geond-team-validate-20260514-015902 --yes`
```
