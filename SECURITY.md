# Security Policy

Geond is alpha software that stores agent transcripts, work evidence, code graph
metadata, reservations, handoffs, and optional usage data. Treat all imported
agent data as potentially sensitive.

## Supported Versions

Only the current `main` branch and the latest alpha release are actively
reviewed. Security fixes may be shipped as patch releases once release tags are
in regular use.

## Reporting A Vulnerability

Use GitHub Private Vulnerability Reporting if it is enabled for this repository.
If private reporting is not available, open a minimal public issue asking for a
private security contact path. Do not include exploit details, secrets, private
transcripts, database URLs, screenshots with tokens, or customer data in a public
issue.

A useful report includes:

- affected version or commit
- affected command, MCP tool, importer, dashboard route, or script
- impact and reproduction steps using synthetic data
- whether secrets, private transcripts, or credentials can be exposed
- suggested fix, if known

## Secret Handling

Do not commit:

- `.env` files
- database dumps or connection strings
- raw VS Code workspaceStorage files
- raw Codex or Claude Code private sessions
- dashboard screenshots that show secrets
- Azure subscription ids, resource ids, tokens, account names, or passwords
- anything under `docs/patent/`, `repo/`, `tmp/`, `result/`, `results/`, or `videos/`

Importers should redact before persistence. Tests should use synthetic secrets
and assert that raw payloads are scrubbed before storage.

## Expected Boundaries

Geond is local-first by default. External embeddings and shared cloud databases
are opt-in. When using a shared PostgreSQL profile, protect the database with
least-privilege credentials, TLS, network restrictions, and short-lived test
resources where possible.

The dashboard is intended as a local read-only observer. It should not display
credential-bearing connection strings, database user info, tokens, or raw auth
headers.

## Known Alpha Gaps

The following are roadmap items, not complete enterprise controls:

- first-class user/team IAM
- row-level security guidance for multi-tenant deployments
- dedicated MCP call input/output audit stream
- external audit sinks such as OpenTelemetry, ELK, Datadog, or CloudWatch
- policy controls for personal productivity surveillance versus team enablement

See [docs/open_source_readiness.md](docs/open_source_readiness.md) for the full
open-source risk review.
