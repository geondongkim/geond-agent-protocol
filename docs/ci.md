# CI Notes

GitHub Actions runs lint, compile, docs link checks, release notes preview,
pytest, and package build against a fresh `pgvector/pgvector:pg16` Postgres
service.

The workflow intentionally sets `GEOND_EMBEDDING_PROVIDER=none` so tests cannot
make external embedding calls. Do not set `GEOND_PRIVACY_MODE=local-only` as a
global CI environment variable. That mode is a behavior under test; setting it
globally changes unit-test semantics and previously broke the Azure OpenAI
provider test by blocking the provider before the deployment-name assertion ran.

When a test is checking cloud-provider wiring rather than privacy policy, create
`Settings` with an explicit `privacy_mode="redacted-cloud"`. When a test is
checking local-only behavior, pass `privacy_mode="local-only"` directly in that
test.

Before editing `.github/workflows/ci.yml`, run the local checks below:

```bash
uv run pre-commit run --all-files
uv run python -m compileall src
uv run python scripts/check_docs_links.py
uv run python scripts/generate_release_notes.py --limit 20 --output release-notes-draft.md
uv run pytest
uv build
```

If CI fails only on GitHub, inspect the failed step first. A failure in `Run tests`
with all setup steps passing usually means the runner environment changed test
behavior, not that dependencies failed to install.