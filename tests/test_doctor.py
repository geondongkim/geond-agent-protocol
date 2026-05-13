from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

from geond.doctor import collect_doctor_report, format_doctor_report, summarize_checks


def fake_runner(command: Sequence[str], timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    del timeout_seconds
    if command[-1] == "--version" and "uv" in command[0]:
        return subprocess.CompletedProcess(command, 0, stdout="uv 0.11.13\n", stderr="")
    if len(command) >= 2 and command[1] == "version" and "docker-compose" in command[0]:
        return subprocess.CompletedProcess(command, 0, stdout="5.1.3\n", stderr="")
    if len(command) >= 2 and command[1] == "version" and "docker" in command[0]:
        return subprocess.CompletedProcess(command, 0, stdout="29.4.3\n", stderr="")
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
        runner=fake_runner,
        which=lambda command: None,
    )

    checks = {check["name"]: check for check in report["checks"]}
    assert checks["docker_default_platform"]["status"] == "warning"
    assert checks["uv"]["status"] == "error"
    assert report["status"] == "error"


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
