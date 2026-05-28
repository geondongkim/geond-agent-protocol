from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import psycopg

from geond.config import database_url_env_names, get_settings

Runner = Callable[[Sequence[str], int], subprocess.CompletedProcess[str]]
Which = Callable[[str], str | None]
LOCAL_POSTGRES_CONTAINER = "geond-postgres"


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


def local_postgres_container_check(docker_path: str, runner: Runner) -> dict[str, Any]:
    ok, output = command_output(
        [
            docker_path,
            "ps",
            "-a",
            "--filter",
            f"name=^/{LOCAL_POSTGRES_CONTAINER}$",
            "--format",
            "{{.Names}}\t{{.Status}}\t{{.Ports}}",
        ],
        runner,
        timeout_seconds=5,
    )
    if not ok:
        return make_check(
            "local_postgres_container",
            "warning",
            f"Could not inspect the local Postgres Docker container: {output or 'no output'}.",
            container=LOCAL_POSTGRES_CONTAINER,
        )

    rows = [row.strip() for row in output.splitlines() if row.strip()]
    if not rows:
        return make_check(
            "local_postgres_container",
            "ok",
            "No existing geond-postgres Docker container name conflict was found.",
            container=LOCAL_POSTGRES_CONTAINER,
        )

    fields = rows[0].split("\t")
    name = fields[0] if fields else LOCAL_POSTGRES_CONTAINER
    status = fields[1] if len(fields) > 1 else ""
    ports = fields[2] if len(fields) > 2 else ""
    lowered_status = status.lower()
    if lowered_status.startswith("up"):
        return make_check(
            "local_postgres_container",
            "ok",
            f"{name} is already running ({status}).",
            container=name,
            docker_status=status,
            ports=ports or None,
        )

    suggestion = f"docker start {LOCAL_POSTGRES_CONTAINER}"
    return make_check(
        "local_postgres_container",
        "warning",
        (
            f"{name} exists but is not running ({status or 'unknown status'}); "
            f"run `{suggestion}` before recreating the compose service."
        ),
        container=name,
        docker_status=status or None,
        ports=ports or None,
        suggested_command=suggestion,
    )


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


def configured_database_env_keys(env_keys: set[str], profile: str) -> list[str]:
    candidates = set(database_url_env_names(profile)) | {"GEOND_DATABASE_URL"}
    return sorted(key for key in candidates if key in env_keys or os.getenv(key))


def collect_doctor_report(
    workspace_root: Path | None = None,
    *,
    check_database: bool = True,
    check_mcp: bool = True,
    check_antigravity: bool = True,
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

    docker_daemon_ok = False
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
            docker_daemon_ok = True
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

    if docker_path and docker_daemon_ok:
        checks.append(local_postgres_container_check(docker_path, runner))

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

    settings = get_settings()
    database_keys = configured_database_env_keys(env_keys, settings.database_profile)
    if database_keys:
        profile_label = settings.database_profile or "default"
        checks.append(
            make_check(
                "database_url",
                "ok",
                f"Database URL is configured for the {profile_label} profile.",
                profile=settings.database_profile or None,
                configured_keys=database_keys,
            )
        )
    else:
        checks.append(
            make_check(
                "database_url",
                "warning",
                "No database URL environment variable is configured.",
            )
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
            resource_templates = (
                getattr(resource_manager, "_templates", {}) if resource_manager is not None else {}
            )
            has_required_tool = "search_dev_memory" in tools
            checks.append(
                make_check(
                    "mcp_registration",
                    "ok" if has_required_tool else "error",
                    (
                        f"MCP registered {len(tools)} tools, {len(resources)} static "
                        f"resources, and {len(resource_templates)} resource templates."
                    ),
                    tool_count=len(tools),
                    resource_count=len(resources),
                    resource_template_count=len(resource_templates),
                )
            )
        except Exception as exc:
            checks.append(make_check("mcp_registration", "error", f"MCP import failed: {exc}"))

    if check_antigravity:
        checks.extend(antigravity_checks(which=which))

    summary = summarize_checks(checks)
    return {
        "status": summary["status"],
        "summary": summary["counts"],
        "workspace_root": str(root),
        "checks": checks,
    }


def antigravity_checks(which: Which = shutil.which) -> list[dict[str, Any]]:
    home = Path.home()
    config_path = home / ".gemini" / "config" / "mcp_config.json"
    antigravity_link = home / ".gemini" / "antigravity" / "mcp_config.json"
    checks: list[dict[str, Any]] = []

    if not config_path.exists():
        checks.append(
            make_check(
                "antigravity_config",
                "warning",
                f"Antigravity MCP config was not found at {config_path}.",
                path=str(config_path),
            )
        )
    else:
        try:
            loaded = json.loads(config_path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError as exc:
            checks.append(
                make_check(
                    "antigravity_config",
                    "error",
                    f"Antigravity MCP config is malformed JSON: {exc.msg}.",
                    path=str(config_path),
                    error=str(exc),
                )
            )
        else:
            servers = loaded.get("mcpServers") if isinstance(loaded, dict) else None
            has_geond = isinstance(servers, dict) and isinstance(servers.get("geond"), dict)
            checks.append(
                make_check(
                    "antigravity_config",
                    "ok" if has_geond else "warning",
                    "Antigravity MCP config contains mcpServers.geond."
                    if has_geond
                    else "Antigravity MCP config is valid JSON but lacks mcpServers.geond.",
                    path=str(config_path),
                    has_geond=has_geond,
                    server_count=len(servers) if isinstance(servers, dict) else 0,
                )
            )

    if antigravity_link.is_symlink():
        try:
            target = antigravity_link.resolve()
        except OSError:
            target = None
        checks.append(
            make_check(
                "antigravity_config_link",
                "ok",
                "Antigravity state config path is a symlink.",
                path=str(antigravity_link),
                target=str(target) if target else None,
            )
        )
    elif antigravity_link.exists():
        checks.append(
            make_check(
                "antigravity_config_link",
                "warning",
                "Antigravity state config path exists but is not a symlink.",
                path=str(antigravity_link),
            )
        )
    else:
        checks.append(
            make_check(
                "antigravity_config_link",
                "warning",
                "Antigravity state config symlink was not found.",
                path=str(antigravity_link),
            )
        )

    checks.append(antigravity_cli_check(which))
    return checks


def antigravity_cli_check(which: Which = shutil.which) -> dict[str, Any]:
    local_appdata = os.environ.get("LOCALAPPDATA")
    appdata = os.environ.get("APPDATA")
    standalone_path = (
        Path(local_appdata) / "agy" / "bin" / "agy.exe"
        if local_appdata
        else Path.home() / "AppData" / "Local" / "agy" / "bin" / "agy.exe"
    )
    shim_path = (
        Path(appdata) / "Antigravity" / "bin" / "agy-node.cmd"
        if appdata
        else Path.home() / "AppData" / "Roaming" / "Antigravity" / "bin" / "agy-node.cmd"
    )
    path_agy = which("agy")

    if standalone_path.exists():
        return make_check(
            "antigravity_cli",
            "ok",
            f"Standalone Antigravity CLI found at {standalone_path}.",
            path=str(standalone_path),
            shim_path=str(shim_path) if shim_path.exists() else None,
            path_agy=path_agy,
        )
    if path_agy and Path(path_agy) != shim_path:
        return make_check(
            "antigravity_cli",
            "ok",
            f"Antigravity CLI found on PATH at {path_agy}.",
            path=path_agy,
            expected_standalone=str(standalone_path),
        )
    if shim_path.exists():
        return make_check(
            "antigravity_cli",
            "warning",
            "Only the Antigravity desktop shim was found; install standalone agy for CLI runs.",
            shim_path=str(shim_path),
            expected_standalone=str(standalone_path),
        )
    return make_check(
        "antigravity_cli",
        "warning",
        "Standalone Antigravity CLI was not found.",
        expected_standalone=str(standalone_path),
        shim_path=str(shim_path),
    )


def format_doctor_report(report: dict[str, Any]) -> str:
    lines = [f"Geond doctor: {report['status']}", ""]
    for check in report["checks"]:
        label = check["status"].upper()
        lines.append(f"[{label}] {check['name']}: {check['message']}")
    return "\n".join(lines)
