# CI Notes

GitHub Actions runs lint, compile, docs link checks, release notes preview,
pytest, a keyword benchmark smoke, and package build against a fresh
`pgvector/pgvector:pg16` Postgres service.

The workflow uploads three small artifacts on successful generation:

- `release-notes-draft`: the deterministic `release-notes-draft.md` preview.
- `geond-ci-benchmark`: `benchmark-smoke.md` and `benchmark-report.md` from a
  seeded sample workspace.
- `python-package-dist`: `uv build` source/wheel artifacts plus
    `dist/SHA256SUMS.txt`.

For `v*` tag pushes, the `release` job waits for the test job, regenerates
release notes with `--since-previous-tag`, and creates or updates the matching
GitHub Release with `release-notes-draft.md` as both the release body and an
attached file. It also rebuilds the package, generates checksums, and attaches
the source distribution, wheel, and `SHA256SUMS.txt` to the release.

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
uv run python -m compileall src scripts
uv run python scripts/check_docs_links.py
uv run python scripts/generate_release_notes.py --limit 20 --output release-notes-draft.md
uv run pytest
uv run geond seed-sample
uv run geond benchmark-search \
    --mode keyword \
    --repeat 3 \
    --limit 5 \
    --workspace-uri "file:///sample/geond" \
    --save \
    --label ci-smoke \
    --format markdown \
    --include-results \
    "service.py database initialization" > benchmark-smoke.md
uv run geond benchmark-report \
    --workspace-uri "file:///sample/geond" \
    --mode keyword \
    --format markdown > benchmark-report.md
uv build
uv run python scripts/write_dist_checksums.py
```

For release tags, validate the notes range locally with:

```bash
uv run python scripts/generate_release_notes.py \
    --since-previous-tag \
    --until v0.1.0-alpha \
    --title "Release v0.1.0-alpha" \
    --output release-notes-draft.md
```

On Windows PowerShell 5.1, `>` writes UTF-16LE. If you need to inspect or
publish the generated markdown locally, pipe to `Out-File -Encoding utf8` or run
the commands under Git Bash/WSL for closer CI parity.

If CI fails only on GitHub, inspect the failed step first. A failure in `Run tests`
with all setup steps passing usually means the runner environment changed test
behavior, not that dependencies failed to install.