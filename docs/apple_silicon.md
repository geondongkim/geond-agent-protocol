# Apple Silicon Setup Notes

Geond should run on Apple Silicon Macs with a native arm64 Python and Docker
Desktop setup. The main risk is accidentally mixing arm64 and x86_64 tooling,
which can cause dependency wheels, Docker images, and local binaries to behave
inconsistently.

## Recommended Path

1. Use a native arm64 terminal. `arch` should print `arm64`. If it prints
   `i386` or `x86_64`, reopen Terminal, iTerm, or VS Code without Rosetta.
2. Install native Homebrew under `/opt/homebrew`, then install `uv`, Git, and
   Python 3.11 or 3.12 from the native toolchain.
3. Clone the repository and create local configuration:

   ```bash
   git clone https://github.com/geondongkim/geond-agent-protocol.git
   cd geond-agent-protocol
   cp .env.example .env
   uv sync --python 3.11
   ```

   If `uv` was just installed and your shell still cannot find it, restart the
   terminal or run `hash -r` in zsh so the command cache sees
   `/opt/homebrew/bin/uv`.

4. Install and start Docker Desktop for Apple Silicon. The project intentionally
   does not pin `platform: linux/amd64`; Docker should pull native arm64 layers
   for `pgvector/pgvector:pg16` and `python:3.12-slim`.
5. Start Postgres and apply the schema:

   ```bash
   docker compose up -d postgres
   docker compose --profile tools run --rm geond-migrate
   uv run geond seed-sample
   ```

   `docker-compose` is also supported when your Docker Desktop installation
   exposes the legacy hyphenated command instead of the `docker compose` plugin.

6. Run the local setup doctor. It checks native arm64 execution, Homebrew and
   `uv` paths, Docker daemon availability, Compose, `.env`, Postgres, pgvector,
   and MCP tool registration without printing secret values:

   ```bash
   uv run geond doctor --format text
   uv run geond mcp-smoke --format text --strict
   ```

7. Run the local checks:

   ```bash
   uv run pre-commit run --all-files
   uv run pytest
   uv run python -m compileall src
   ```

## CLI and MCP Smoke Checks

After `seed-sample`, verify that the database-backed CLI path can read from the
local Postgres instance without making embedding calls:

```bash
uv run geond search app_context \
   --mode keyword \
   --workspace-uri file:///sample/geond \
   --limit 3

uv run geond explain-change service.py --limit 3
```

For automation, use strict doctor mode. It exits non-zero only when an error is
found, while warnings still appear in the JSON output for follow-up:

```bash
uv run geond doctor --format json --strict
```

MCP clients should launch the server with `uv --directory <repo> run geond-mcp`.
The doctor command imports the MCP server and verifies that the expected tool
registry is populated. `geond mcp-smoke` goes one step further: it starts the
stdio server as a subprocess, initializes it with the MCP client SDK, lists
tools/resources, reads `geond://sessions`, and calls `search_dev_memory` in
keyword mode against the seeded workspace.

## Cautions

- Do not set `DOCKER_DEFAULT_PLATFORM=linux/amd64` unless you are deliberately
  testing emulation. If it is already set, unset it before running Docker:

  ```bash
  unset DOCKER_DEFAULT_PLATFORM
  ```

- Avoid mixing `/usr/local` x86_64 Homebrew packages with `/opt/homebrew` arm64
  packages in the same shell. Check `which python`, `which uv`, and
  `python -c 'import platform; print(platform.machine())'` when debugging.
- VS Code terminals can load `.env` automatically when workspace settings set
   `python.envFile` to `${workspaceFolder}/.env` and
   `python.terminal.useEnvFile` to `true`. Keep that setting local if it contains
   machine-specific paths.
- If a Python dependency falls back to building from source, install Apple
  command line tools with `xcode-select --install` and rerun `uv sync`.
- Keep `.claude/` local. It may contain per-machine agent state and should not
  be committed.
- If Docker reports an architecture mismatch or starts slowly, remove stale
  amd64 images for this project and let Docker pull fresh arm64 images.
- The GitHub Actions workflow validates Linux CI, not macOS arm64. Use the local
  checks above on the MacBook as the source of truth for Apple Silicon readiness.
