# Windows Client Validation

Run id: 20260514-015902

Source the local connection file that is intentionally ignored by git:

~~~powershell
. .\docs\azure_validation\20260514-015902\connection.local.ps1
~~~

Validated from this Windows client:

~~~powershell
uv run geond migrate
uv run geond seed-sample
uv run geond reserve-files 7b786436-73e2-4d23-a4a4-f724eaf96c3d --agent-name windows-codex --file docs/agent_activity_dashboard.md --purpose "Azure shared DB validation"
uv run geond reserve-symbols 7b786436-73e2-4d23-a4a4-f724eaf96c3d --agent-name windows-codex --symbol geond.dashboard.sessions --purpose "Azure shared DB validation"
uv run geond record-handoff 7b786436-73e2-4d23-a4a4-f724eaf96c3d --from-agent windows-codex --to-agent macbook-agent --summary "Windows client wrote this handoff into shared Azure PostgreSQL." --next-action "MacBook should list handoffs, conflicts, search memory, and inspect symbol context."
uv run geond dashboard-overview 7b786436-73e2-4d23-a4a4-f724eaf96c3d --limit 10
uv run geond dashboard-events 7b786436-73e2-4d23-a4a4-f724eaf96c3d --limit 10
~~~

Observed counts: sessions=1, open_handoffs=1, active_file_reservations=1, active_symbol_reservations=1.
