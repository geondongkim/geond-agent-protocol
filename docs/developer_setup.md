# Developer Setup

This page lists the local tools needed for Geond development and quick commands
to verify an installation before running the demo, tests, or MCP server.

## Required Tools

| Tool | Why Geond needs it | Verify |
| --- | --- | --- |
| Python 3.11+ | Runs the CLI, MCP server, tests, and local collectors. | `python --version` |
| uv | Creates the virtual environment and runs project commands. | `uv --version` |
| Docker + Compose | Runs local Postgres with pgvector. | `docker version` and `docker compose version` |
| Git | Reads commits, diffs, and workspace fingerprints. | `git --version` |
| ripgrep | Fast repository search for contributors and coding agents. | `rg --version` |

Optional but useful for shared-database validation and migration:

| Tool | Why Geond may need it | Verify |
| --- | --- | --- |
| PostgreSQL client tools | Export/import Geond data with `pg_dump` and `psql` when validating a shared PostgreSQL-compatible database. | `pg_dump --version` and `psql --version` |

Optional but useful for LSP reference collection:

| Language | Geond profile | Example server | Install check |
| --- | --- | --- |
| Python | `pyright` | `pyright-langserver --stdio` | `pyright-langserver --version` |
| TypeScript/JavaScript | `typescript` | `typescript-language-server --stdio` | `typescript-language-server --version` |

Geond's `collect-lsp-references` command works with any stdio language server
that implements `textDocument/references`. Use `--server-profile auto` for
Python, TypeScript, and JavaScript paths, or pass `--server-command` for another
language server. Run `uv run geond lsp-server-profiles` to list built-in profile
commands and install hints.

## Windows

Install Git, Docker Desktop, Python, uv, ripgrep, and optionally PostgreSQL
client tools. With `winget`, one common setup path is:

```powershell
winget install --id Git.Git -e
winget install --id Docker.DockerDesktop -e
winget install --id Python.Python.3.11 -e
winget install --id BurntSushi.ripgrep.MSVC -e
winget install --id PostgreSQL.PostgreSQL.16 -e
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Restart the terminal after installing uv, ripgrep, or PostgreSQL client tools so
PATH changes are loaded. Then verify the tools:

```powershell
python --version
uv --version
git --version
rg --version
docker version
docker compose version
pg_dump --version
psql --version
```

Docker Desktop must be running before `docker compose up -d postgres`.

## macOS

Install native Homebrew first. On Apple Silicon, Homebrew should live under
`/opt/homebrew`; see [apple_silicon.md](apple_silicon.md) for Rosetta and
multi-arch Docker notes.

```bash
brew install python@3.11 uv git ripgrep libpq
brew install --cask docker
```

If Homebrew does not link `pg_dump` and `psql`, add `libpq` to your shell path
as printed by `brew info libpq`.

Start Docker Desktop, then verify:

```bash
python3.11 --version
uv --version
git --version
rg --version
docker version
docker compose version
pg_dump --version
psql --version
```

If Python packages fall back to source builds on a fresh Mac, install command
line tools with `xcode-select --install`.

## Linux

Use your distribution packages for Python, Git, ripgrep, and Docker Engine, then
install uv from Astral's installer.

Ubuntu/Debian example:

```bash
sudo apt-get update
sudo apt-get install -y python3.11 python3.11-venv git ripgrep docker.io docker-compose-plugin postgresql-client
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Verify:

```bash
python3.11 --version
uv --version
git --version
rg --version
docker version
docker compose version
pg_dump --version
psql --version
```

If Docker requires sudo, either run Docker commands with sudo or add your user to
the Docker group according to your distribution's guidance.

## Project Health Check

After cloning the repository and installing the required tools:

```bash
uv sync
docker compose up -d postgres
docker compose --profile tools run --rm geond-migrate
uv run geond doctor --format text
uv run pytest
```

`geond doctor` verifies `.env`, Docker, Compose, Postgres, pgvector, embedding
configuration, and MCP tool/resource registration.

## Database Profiles

Geond reads `.env` automatically. `GEOND_DATABASE_URL` is the default active
database URL, and `GEOND_DATABASE_PROFILE` can switch to profile-specific URLs
without editing the default local value.

```bash
GEOND_DATABASE_PROFILE=local
GEOND_DATABASE_URL=postgresql://geond:geond_dev_password@localhost:55432/geond
AZURE_GEOND_DATABASE_URL=postgresql://geondadmin:<password>@<server>.postgres.database.azure.com:5432/geond?sslmode=require
```

Set `GEOND_DATABASE_PROFILE=azure` to use `AZURE_GEOND_DATABASE_URL`. Additional
profiles can use either `GEOND_DATABASE_URL_<PROFILE>` or
`GEOND_<PROFILE>_DATABASE_URL`, for example `GEOND_DATABASE_URL_TEAM_BLUE`.

The dashboard uses the same active profile and shows safe source metadata in
`/health` and `/api`. Its Sessions view also keeps raw stored messages separate
from readable captured prompts, which helps older imported VS Code/Copilot
sessions stay useful even when the latest window is mostly tool traces.
