from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import psycopg

from geond.config import get_settings

Runner = Callable[[Sequence[str], int], subprocess.CompletedProcess[str]]
Which = Callable[[str], str | None]


def default_runner(
    command: Sequence[str],
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )


def make_check(name: str, status: str, message: str, **metadata: Any) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "message": message,
        "metadata": {key: value for key, value in metadata.items() if value is not None},
    }


def summarize_checks(checks: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"ok": 0, "warning": 0, "error": 0}
    for check in checks:
        counts[check["status"]] = counts.get(check["status"], 0) + 1
    status = "error" if counts["error"] else "warning" if counts["warning"] else "ok"
    return {"status": status, "counts": counts}


def command_output(
    command: Sequence[str],
    runner: Runner,
    timeout_seconds: int = 5,
) -> tuple[bool, str]:
    try:
        completed = runner(command, timeout_seconds)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    output = (completed.stdout or completed.stderr or "").strip()
    return completed.returncode == 0, output


def read_env_keys(env_path: Path) -> set[str]:
    keys: set[str] = set()
    if not env_path.exists():
        return keys
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _ = stripped.split("=", 1)
        keys.add(key.strip())
    return keys


def collect_doctor_report(
    workspace_root: Path | None = None,
    *,
    check_database: bool = True,
    check_mcp: bool = True,
    runner: Runner = default_runner,
    which: Which = shutil.which,
) -> dict[str, Any]:
    root = workspace_root or Path.cwd()
    checks: list[dict[str, Any]] = []
    system_name = platform.system()
    machine = platform.machine()

    if system_name == "Darwin" and machine != "arm64":
        checks.append(
            make_check(
                "platform",
                "warning",
                f"macOS is running as {machine}; use a native arm64 shell on Apple Silicon.",
                system=system_name,
                machine=machine,
            )
        )
    else:
        checks.append(
            make_check(
                "platform",
                "ok",
                f"Detected {system_name or 'unknown'} {machine or 'unknown'}.",
                system=system_name,
                machine=machine,
            )
        )

    checks.append(
        make_check(
            "python",
            "ok",
            f"Python {platform.python_version()} is running from {sys.executable}.",
            executable=sys.executable,
            version=platform.python_version(),
        )
    )

    brew_path = which("brew")
    if system_name == "Darwin":
        if not brew_path:
            checks.append(
                make_check(
                    "homebrew",
                    "warning",
                    "Homebrew is not on PATH; install native arm64 Homebrew under /opt/homebrew.",
                )
            )
        elif machine == "arm64" and not brew_path.startswith("/opt/homebrew/"):
            checks.append(
                make_check(
                    "homebrew",
                    "warning",
                    f"Homebrew is at {brew_path}; "
                    "Apple Silicon setups should prefer /opt/homebrew.",
                    path=brew_path,
                )
            )
        else:
            checks.append(
                make_check(
                    "homebrew",
                    "ok",
                    f"Homebrew found at {brew_path}.",
                    path=brew_path,
                )
            )

    uv_path = which("uv")
    if not uv_path:
        checks.append(
            make_check(
                "uv",
                "error",
                "uv is not on PATH; install it with `brew install uv`.",
            )
        )
    else:
        ok, output = command_output([uv_path, "--version"], runner)
        status = "ok" if ok else "warning"
        message = (
            output if ok else f"uv was found at {uv_path}, but `uv --version` failed: {output}"
        )
        checks.append(make_check("uv", status, message, path=uv_path))

    docker_platform = os.getenv("DOCKER_DEFAULT_PLATFORM", "")
    if system_name == "Darwin" and machine == "arm64" and docker_platform == "linux/amd64":
        checks.append(
            make_check(
                "docker_default_platform",
                "warning",
                "DOCKER_DEFAULT_PLATFORM=linux/amd64 forces emulation; "
                "unset it for native arm64 images.",
                value=docker_platform,
            )
        )
    else:
        checks.append(
            make_check(
                "docker_default_platform",
                "ok",
                "DOCKER_DEFAULT_PLATFORM does not force amd64 emulation.",
                value=docker_platform or None,
            )
        )

    docker_path = which("docker")
    if not docker_path:
        checks.append(make_check("docker_cli", "error", "docker is not on PATH."))
    else:
        checks.append(
            make_check(
                "docker_cli",
                "ok",
                f"docker found at {docker_path}.",
                path=docker_path,
            )
        )
        ok, output = command_output(
            [docker_path, "version", "--format", "{{.Server.Version}}"],
            runner,
            timeout_seconds=10,
        )
        if ok and output:
            checks.append(make_check("docker_daemon", "ok", f"Docker daemon is running: {output}."))
        else:
            checks.append(
                make_check(
                    "docker_daemon",
                    "error",
                    f"Docker daemon is not responding: {output or 'no output'}.",
                )
            )

    compose_path = which("docker-compose")
    if compose_path:
        ok, output = command_output([compose_path, "version", "--short"], runner)
        checks.append(
            make_check(
                "docker_compose",
                "ok" if ok else "warning",
                output if ok else f"docker-compose was found, but version check failed: {output}",
                path=compose_path,
                command="docker-compose",
            )
        )
    elif docker_path:
        ok, output = command_output([docker_path, "compose", "version", "--short"], runner)
        checks.append(
            make_check(
                "docker_compose",
                "ok" if ok else "error",
                output
                if ok
                else f"Docker Compose is not available through docker compose: {output}",
                command="docker compose",
            )
        )
    else:
        checks.append(make_check("docker_compose", "error", "Docker Compose is not available."))

    env_path = root / ".env"
    env_keys = read_env_keys(env_path)
    if env_path.exists():
        checks.append(make_check("env_file", "ok", f"Found {env_path.name}.", path=str(env_path)))
    else:
        checks.append(
            make_check(
                "env_file",
                "warning",
                f"Missing {env_path.name}; copy .env.example first.",
            )
        )

    if "GEOND_DATABASE_URL" in env_keys or os.getenv("GEOND_DATABASE_URL"):
        checks.append(make_check("database_url", "ok", "GEOND_DATABASE_URL is configured."))
    else:
        checks.append(
            make_check("database_url", "warning", "GEOND_DATABASE_URL is not configured.")
        )

    embedding_provider = os.getenv("GEOND_EMBEDDING_PROVIDER") or get_settings().embedding_provider
    api_key_present = any(
        key in env_keys or os.getenv(key)
        for key in ("GEOND_EMBEDDING_API_KEY", "OPENAI_API_KEY", "GEOND_AZURE_OPENAI_API_KEY")
    )
    checks.append(
        make_check(
            "embedding_config",
            "ok",
            f"Embedding provider is {embedding_provider}; "
            f"API key present: {bool(api_key_present)}.",
            provider=embedding_provider,
            api_key_present=bool(api_key_present),
        )
    )

    if check_database:
        settings = get_settings()
        try:
            with psycopg.connect(settings.database_url, connect_timeout=3) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.execute("SELECT to_regclass('public.messages') IS NOT NULL")
                    schema_loaded = bool(cur.fetchone()[0])
                    cur.execute(
                        "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')"
                    )
                    vector_loaded = bool(cur.fetchone()[0])
            db_status = "ok" if schema_loaded and vector_loaded else "warning"
            checks.append(
                make_check(
                    "postgres",
                    db_status,
                    "Postgres is reachable; schema and pgvector are available."
                    if db_status == "ok"
                    else "Postgres is reachable, but schema or pgvector is missing.",
                    schema_loaded=schema_loaded,
                    vector_loaded=vector_loaded,
                )
            )
        except psycopg.Error as exc:
            checks.append(make_check("postgres", "error", f"Postgres connection failed: {exc}"))

    if check_mcp:
        try:
            from geond.mcp_server import server

            tool_manager = getattr(server.mcp, "_tool_manager", None)
            resource_manager = getattr(server.mcp, "_resource_manager", None)
            tools = getattr(tool_manager, "_tools", {}) if tool_manager is not None else {}
            resources = (
                getattr(resource_manager, "_resources", {}) if resource_manager is not None else {}
            )
            has_required_tool = "search_dev_memory" in tools
            checks.append(
                make_check(
                    "mcp_registration",
                    "ok" if has_required_tool else "error",
                    f"MCP registered {len(tools)} tools and {len(resources)} resources.",
                    tool_count=len(tools),
                    resource_count=len(resources),
                )
            )
        except Exception as exc:
            checks.append(make_check("mcp_registration", "error", f"MCP import failed: {exc}"))

    summary = summarize_checks(checks)
    return {
        "status": summary["status"],
        "summary": summary["counts"],
        "workspace_root": str(root),
        "checks": checks,
    }


def format_doctor_report(report: dict[str, Any]) -> str:
    lines = [f"Geond doctor: {report['status']}", ""]
    for check in report["checks"]:
        label = check["status"].upper()
        lines.append(f"[{label}] {check['name']}: {check['message']}")
    return "\n".join(lines)
