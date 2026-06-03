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
attached file. It also rebuilds the package, generates checksums, signs the
source distribution, wheel, and checksum manifest with Sigstore keyless signing,
and attaches the source distribution, wheel, `SHA256SUMS.txt`, and
`*.sigstore.json` bundles to the release. The release job grants `id-token:
write` only for this signing step and uploads the signing bundle set as a
workflow artifact for auditability.

To verify a downloaded artifact, install `sigstore` and use the tag-specific
GitHub Actions identity. For example, replace the version and filename below:

```bash
sigstore verify identity \
    --cert-identity "https://github.com/geondongkim/geond-agent-protocol/.github/workflows/ci.yml@refs/tags/v0.1.0" \
    --cert-oidc-issuer "https://token.actions.githubusercontent.com" \
    --bundle geond_agent_protocol-0.1.0-py3-none-any.whl.sigstore.json \
    geond_agent_protocol-0.1.0-py3-none-any.whl
```

PyPI publishing is available as a manual trusted publishing workflow. Run the
`Publish to PyPI` workflow from GitHub Actions with the `v*` release tag after
the release workflow succeeds. The workflow checks out that tag, builds package
artifacts in a restricted build job, stages only `*.tar.gz` and `*.whl` into
`publish-dist/`, then publishes that directory from a separate `publish-pypi`
job with `pypa/gh-action-pypi-publish@release/v1`. The checksum manifest and
Sigstore bundles stay on the GitHub Release and are not sent to PyPI.

Before running the workflow, configure a PyPI trusted publisher for the
`geond-agent-protocol` project with these values:

- Repository owner/name: `geondongkim/geond-agent-protocol`
- Workflow name: `publish-pypi.yml`
- Environment name: leave empty unless you deliberately add a GitHub environment
  gate to the workflow
- Package name: `geond-agent-protocol`

The publish job grants `id-token: write` only in that job, relies on PyPI OIDC
instead of API tokens, and prints upload hashes for release audit trails. If you
later add a GitHub environment approval gate, configure the same environment
name in the PyPI trusted publisher.

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
    --until v0.1.0 \
    --title "Release v0.1.0" \
    --output release-notes-draft.md
```

On Windows PowerShell 5.1, `>` writes UTF-16LE. If you need to inspect or
publish the generated markdown locally, pipe to `Out-File -Encoding utf8` or run
the commands under Git Bash/WSL for closer CI parity.

If CI fails only on GitHub, inspect the failed step first. A failure in `Run tests`
with all setup steps passing usually means the runner environment changed test
behavior, not that dependencies failed to install.
