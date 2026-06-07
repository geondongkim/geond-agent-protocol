from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from geond.doctor import collect_doctor_report, format_doctor_report, summarize_checks


def fake_runner(command: Sequence[str], timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    del timeout_seconds
    if command[-1] == "--version" and "uv" in command[0]:
        return subprocess.CompletedProcess(command, 0, stdout="uv 0.11.13\n", stderr="")
    if len(command) >= 3 and command[1] == "compose" and command[2] == "version":
        return subprocess.CompletedProcess(command, 0, stdout="5.1.3\n", stderr="")
    if len(command) >= 2 and command[1] == "version" and "docker-compose" in command[0]:
        return subprocess.CompletedProcess(command, 0, stdout="5.1.3\n", stderr="")
    if len(command) >= 2 and command[1] == "version" and "docker" in command[0]:
        return subprocess.CompletedProcess(command, 0, stdout="29.4.3\n", stderr="")
    if len(command) >= 2 and command[1] == "ps" and "docker" in command[0]:
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
    return subprocess.CompletedProcess(command, 1, stdout="", stderr="unexpected command")


def test_collect_doctor_report_for_native_macos_tooling(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / ".env").write_text(
        "GEOND_DATABASE_URL=postgresql://geond:geond_dev_password@localhost:55432/geond\n"
        "GEOND_EMBEDDING_PROVIDER=none\n",
        encoding="utf-8",
    )

    paths = {
        "brew": "/opt/homebrew/bin/brew",
        "uv": "/opt/homebrew/bin/uv",
        "docker": "/usr/local/bin/docker",
        "docker-compose": "/usr/local/bin/docker-compose",
    }

    monkeypatch.setattr("geond.doctor.platform.system", lambda: "Darwin")
    monkeypatch.setattr("geond.doctor.platform.machine", lambda: "arm64")
    monkeypatch.delenv("DOCKER_DEFAULT_PLATFORM", raising=False)

    report = collect_doctor_report(
        tmp_path,
        check_database=False,
        check_mcp=False,
        check_antigravity=False,
        runner=fake_runner,
        which=paths.get,
    )

    statuses = {check["name"]: check["status"] for check in report["checks"]}
    assert report["status"] == "ok"
    assert statuses["platform"] == "ok"
    assert statuses["homebrew"] == "ok"
    assert statuses["uv"] == "ok"
    assert statuses["docker_daemon"] == "ok"
    assert statuses["docker_compose"] == "ok"
    assert statuses["env_file"] == "ok"
    assert statuses["database_url"] == "ok"


def test_collect_doctor_report_uses_macos_docker_desktop_cli_when_not_on_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / ".env").write_text(
        "GEOND_DATABASE_URL=postgresql://geond:geond_dev_password@localhost:55432/geond\n"
        "GEOND_EMBEDDING_PROVIDER=none\n",
        encoding="utf-8",
    )
    docker_desktop = tmp_path / "Docker.app" / "Contents" / "Resources" / "bin" / "docker"
    docker_desktop.parent.mkdir(parents=True)
    docker_desktop.write_text("", encoding="utf-8")

    paths = {
        "brew": "/opt/homebrew/bin/brew",
        "uv": "/opt/homebrew/bin/uv",
    }

    monkeypatch.setattr("geond.doctor.platform.system", lambda: "Darwin")
    monkeypatch.setattr("geond.doctor.platform.machine", lambda: "arm64")
    monkeypatch.setattr("geond.doctor.MACOS_DOCKER_DESKTOP_CLI", docker_desktop)
    monkeypatch.delenv("DOCKER_DEFAULT_PLATFORM", raising=False)

    report = collect_doctor_report(
        tmp_path,
        check_database=False,
        check_mcp=False,
        check_antigravity=False,
        runner=fake_runner,
        which=paths.get,
    )

    checks = {check["name"]: check for check in report["checks"]}
    assert report["status"] == "ok"
    assert checks["docker_cli"]["status"] == "ok"
    assert checks["docker_cli"]["metadata"]["source"] == "docker_desktop"
    assert checks["docker_cli"]["metadata"]["path"] == str(docker_desktop)
    assert checks["docker_daemon"]["status"] == "ok"
    assert checks["docker_compose"]["status"] == "ok"
    assert checks["docker_compose"]["metadata"]["command"] == "docker compose"


def test_collect_doctor_report_accepts_profile_database_url(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / ".env").write_text(
        "GEOND_DATABASE_PROFILE=azure\n"
        "AZURE_GEOND_DATABASE_URL=postgresql://example/geond?sslmode=require\n"
        "GEOND_EMBEDDING_PROVIDER=none\n",
        encoding="utf-8",
    )

    monkeypatch.setattr("geond.doctor.platform.system", lambda: "Linux")
    monkeypatch.setattr("geond.doctor.platform.machine", lambda: "x86_64")
    monkeypatch.setenv("GEOND_DATABASE_PROFILE", "azure")
    monkeypatch.setenv("AZURE_GEOND_DATABASE_URL", "postgresql://example/geond?sslmode=require")

    report = collect_doctor_report(
        tmp_path,
        check_database=False,
        check_mcp=False,
        check_antigravity=False,
        runner=fake_runner,
        which=lambda command: f"/usr/bin/{command}",
    )

    checks = {check["name"]: check for check in report["checks"]}
    assert checks["database_url"]["status"] == "ok"
    assert checks["database_url"]["metadata"]["profile"] == "azure"
    assert "AZURE_GEOND_DATABASE_URL" in checks["database_url"]["metadata"]["configured_keys"]


def test_collect_doctor_report_warns_on_amd64_emulation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("geond.doctor.platform.system", lambda: "Darwin")
    monkeypatch.setattr("geond.doctor.platform.machine", lambda: "arm64")
    monkeypatch.setenv("DOCKER_DEFAULT_PLATFORM", "linux/amd64")

    report = collect_doctor_report(
        tmp_path,
        check_database=False,
        check_mcp=False,
        check_antigravity=False,
        runner=fake_runner,
        which=lambda command: None,
    )

    checks = {check["name"]: check for check in report["checks"]}
    assert checks["docker_default_platform"]["status"] == "warning"
    assert checks["uv"]["status"] == "error"
    assert report["status"] == "error"


def test_collect_doctor_report_warns_on_stopped_geond_postgres(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / ".env").write_text(
        "GEOND_DATABASE_URL=postgresql://geond:geond_dev_password@localhost:55432/geond\n"
        "GEOND_EMBEDDING_PROVIDER=none\n",
        encoding="utf-8",
    )

    def runner(command: Sequence[str], timeout_seconds: int) -> subprocess.CompletedProcess[str]:
        del timeout_seconds
        if len(command) >= 2 and command[1] == "ps" and "docker" in command[0]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="geond-postgres\tExited (0) 2 hours ago\t0.0.0.0:55432->5432/tcp\n",
                stderr="",
            )
        return fake_runner(command, 5)

    monkeypatch.setattr("geond.doctor.platform.system", lambda: "Linux")
    monkeypatch.setattr("geond.doctor.platform.machine", lambda: "x86_64")
    monkeypatch.delenv("DOCKER_DEFAULT_PLATFORM", raising=False)

    paths = {
        "uv": "/usr/bin/uv",
        "docker": "/usr/bin/docker",
        "docker-compose": "/usr/bin/docker-compose",
    }
    report = collect_doctor_report(
        tmp_path,
        check_database=False,
        check_mcp=False,
        check_antigravity=False,
        runner=runner,
        which=paths.get,
    )

    checks = {check["name"]: check for check in report["checks"]}
    local_postgres = checks["local_postgres_container"]
    assert local_postgres["status"] == "warning"
    assert "docker start geond-postgres" in local_postgres["message"]
    assert local_postgres["metadata"]["suggested_command"] == "docker start geond-postgres"


def test_format_doctor_report() -> None:
    report = {
        "status": "warning",
        "checks": [
            {"name": "platform", "status": "ok", "message": "Detected Darwin arm64."},
            {"name": "postgres", "status": "warning", "message": "Postgres is not ready."},
        ],
    }

    output = format_doctor_report(report)

    assert "Geond doctor: warning" in output
    assert "[OK] platform" in output
    assert "[WARNING] postgres" in output


def test_doctor_counts_fastmcp_resource_templates(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("geond.doctor.platform.system", lambda: "Linux")
    monkeypatch.setattr("geond.doctor.platform.machine", lambda: "x86_64")
    monkeypatch.delenv("DOCKER_DEFAULT_PLATFORM", raising=False)

    report = collect_doctor_report(
        tmp_path,
        check_database=False,
        check_mcp=True,
        check_antigravity=False,
        runner=fake_runner,
        which=lambda command: f"/usr/bin/{command}",
    )

    checks = {check["name"]: check for check in report["checks"]}
    mcp_check = checks["mcp_registration"]
    assert mcp_check["status"] == "ok"
    assert mcp_check["metadata"]["tool_count"] >= 20
    assert mcp_check["metadata"]["resource_count"] >= 2
    assert mcp_check["metadata"]["resource_template_count"] >= 1


def test_summarize_checks() -> None:
    summary = summarize_checks(
        [
            {"status": "ok"},
            {"status": "warning"},
            {"status": "error"},
        ]
    )

    assert summary["status"] == "error"
    assert summary["counts"] == {"ok": 1, "warning": 1, "error": 1}


def test_collect_doctor_report_checks_antigravity_config_and_cli(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    config = home / ".gemini" / "config" / "mcp_config.json"
    config.parent.mkdir(parents=True)
    config.write_text('{"mcpServers":{"geond":{"command":"uv"}}}', encoding="utf-8")
    link = home / ".gemini" / "antigravity" / "mcp_config.json"
    link.parent.mkdir(parents=True)
    try:
        link.symlink_to(config)
    except OSError as exc:
        pytest.skip(f"Symlink creation is not available: {exc}")
    local_appdata = tmp_path / "local"
    agy = local_appdata / "agy" / "bin" / "agy.exe"
    agy.parent.mkdir(parents=True)
    agy.write_text("", encoding="utf-8")

    monkeypatch.setattr("geond.doctor.Path.home", lambda: home)
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))

    report = collect_doctor_report(
        tmp_path,
        check_database=False,
        check_mcp=False,
        check_antigravity=True,
        runner=fake_runner,
        which=lambda command: None,
    )

    checks = {check["name"]: check for check in report["checks"]}
    assert checks["antigravity_config"]["status"] == "ok"
    assert checks["antigravity_config_link"]["status"] == "ok"
    assert checks["antigravity_cli"]["status"] == "ok"
