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

4. Install and start Docker Desktop for Apple Silicon. The project intentionally
   does not pin `platform: linux/amd64`; Docker should pull native arm64 layers
   for `pgvector/pgvector:pg16` and `python:3.12-slim`.
5. Start Postgres and apply the schema:

   ```bash
   docker compose up -d postgres
   docker compose --profile tools run --rm geond-migrate
   uv run geond seed-sample
   ```

6. Run the local checks:

   ```bash
   uv run pre-commit run --all-files
   uv run pytest
   uv run python -m compileall src
   ```

## Cautions

- Do not set `DOCKER_DEFAULT_PLATFORM=linux/amd64` unless you are deliberately
  testing emulation. If it is already set, unset it before running Docker:

  ```bash
  unset DOCKER_DEFAULT_PLATFORM
  ```

- Avoid mixing `/usr/local` x86_64 Homebrew packages with `/opt/homebrew` arm64
  packages in the same shell. Check `which python`, `which uv`, and
  `python -c 'import platform; print(platform.machine())'` when debugging.
- If a Python dependency falls back to building from source, install Apple
  command line tools with `xcode-select --install` and rerun `uv sync`.
- Keep `.claude/` local. It may contain per-machine agent state and should not
  be committed.
- If Docker reports an architecture mismatch or starts slowly, remove stale
  amd64 images for this project and let Docker pull fresh arm64 images.
- The GitHub Actions workflow validates Linux CI, not macOS arm64. Use the local
  checks above on the MacBook as the source of truth for Apple Silicon readiness.