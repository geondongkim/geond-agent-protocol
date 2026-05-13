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

Optional but useful for LSP reference collection:

| Language | Example server | Install check |
| --- | --- | --- |
| Python | `pyright-langserver --stdio` | `pyright-langserver --version` |
| TypeScript/JavaScript | `typescript-language-server --stdio` | `typescript-language-server --version` |

Geond's `collect-lsp-references` command works with any stdio language server
that implements `textDocument/references`; the server command is supplied at
runtime, so the project does not require a specific language server package.

## Windows

Install Git, Docker Desktop, Python, uv, and ripgrep. With `winget`, one common
setup path is:

```powershell
winget install --id Git.Git -e
winget install --id Docker.DockerDesktop -e
winget install --id Python.Python.3.11 -e
winget install --id BurntSushi.ripgrep.MSVC -e
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Restart the terminal after installing uv or ripgrep so PATH changes are loaded.
Then verify the tools:

```powershell
python --version
uv --version
git --version
rg --version
docker version
docker compose version
```

Docker Desktop must be running before `docker compose up -d postgres`.

## macOS

Install native Homebrew first. On Apple Silicon, Homebrew should live under
`/opt/homebrew`; see [apple_silicon.md](apple_silicon.md) for Rosetta and
multi-arch Docker notes.

```bash
brew install python@3.11 uv git ripgrep
brew install --cask docker
```

Start Docker Desktop, then verify:

```bash
python3.11 --version
uv --version
git --version
rg --version
docker version
docker compose version
```

If Python packages fall back to source builds on a fresh Mac, install command
line tools with `xcode-select --install`.

## Linux

Use your distribution packages for Python, Git, ripgrep, and Docker Engine, then
install uv from Astral's installer.

Ubuntu/Debian example:

```bash
sudo apt-get update
sudo apt-get install -y python3.11 python3.11-venv git ripgrep docker.io docker-compose-plugin
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
