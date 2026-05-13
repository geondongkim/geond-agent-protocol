# MacBook Apple Silicon Client Validation

Run id: 20260514-015902
Seed workspace id: 7b786436-73e2-4d23-a4a4-f724eaf96c3d
Imported project workspace id: be7a3558-adda-4c2a-adf1-2c4181ef1b2b

Ask the Windows owner for the temporary password through a private channel. Do
not commit the password. The resource group is intentionally temporary and
should be deleted after this MacBook validation.

~~~bash
git clone https://github.com/geondongkim/geond-agent-protocol.git
cd geond-agent-protocol
uv sync
export GEOND_DATABASE_URL='postgresql://geondadmin:<password>@pg-geond-team-20260514015902.postgres.database.azure.com:5432/geond?sslmode=require'
export GEOND_PRIVACY_MODE=local-only

uv run geond doctor
uv run geond dashboard-overview 7b786436-73e2-4d23-a4a4-f724eaf96c3d --limit 20
uv run geond dashboard-events 7b786436-73e2-4d23-a4a4-f724eaf96c3d --limit 20
uv run geond dashboard-overview be7a3558-adda-4c2a-adf1-2c4181ef1b2b --limit 20
uv run geond dashboard-events be7a3558-adda-4c2a-adf1-2c4181ef1b2b --limit 20
uv run geond summarize-changeset 54042b2
uv run geond conflicts 7b786436-73e2-4d23-a4a4-f724eaf96c3d
uv run geond list-handoffs 7b786436-73e2-4d23-a4a4-f724eaf96c3d
uv run geond search --workspace-uri file:///sample/geond --mode keyword app_context
uv run geond search --workspace-uri file:///C:/Users/EL035/dataschool/RealMe_OPIc --mode keyword dashboard
uv run geond benchmark-search --workspace-uri file:///sample/geond --mode keyword --repeat 3 --limit 5 app_context
uv run geond dashboard serve --host 127.0.0.1 --port 8879
~~~

Expected result: the MacBook sees the Windows-created session, messages,
reservations, handoff, imported project sessions, commit `54042b2` evidence,
dashboard overview, sessions endpoint, and event stream from the same shared
Azure PostgreSQL database without using cloud embedding calls.

For an agent-assisted MacBook pass, paste
[macbook_copilot_prompt.md](macbook_copilot_prompt.md) into GitHub Copilot Chat
on the MacBook after sharing the temporary database password privately.
