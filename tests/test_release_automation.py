from __future__ import annotations

import importlib.util
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType


def load_script_module(name: str, relative_path: str) -> ModuleType:
    path = Path(__file__).parents[1] / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


check_docs_links_module = load_script_module("check_docs_links", "scripts/check_docs_links.py")
release_notes_module = load_script_module(
    "generate_release_notes",
    "scripts/generate_release_notes.py",
)
checksums_module = load_script_module(
    "write_dist_checksums",
    "scripts/write_dist_checksums.py",
)
check_docs_links = check_docs_links_module.check_docs_links
Commit = release_notes_module.Commit
format_release_notes = release_notes_module.format_release_notes
previous_tag = release_notes_module.previous_tag
write_checksums = checksums_module.write_checksums


def assert_action_major_at_least(workflow: str, action: str, minimum_major: int) -> None:
    action_ref_pattern = rf"uses:\s*{re.escape(action)}@v(\d+)\b"
    majors = [int(match) for match in re.findall(action_ref_pattern, workflow)]

    assert majors, f"{action} is not referenced in workflow"
    assert all(major >= minimum_major for major in majors), majors


def test_check_docs_links_reports_missing_local_targets(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "ok.md").write_text("# OK\n", encoding="utf-8")
    (tmp_path / "README.md").write_text(
        "[ok](docs/ok.md)\n[missing](docs/missing.md)\n[external](https://example.com)\n",
        encoding="utf-8",
    )

    broken = check_docs_links(tmp_path, include_patterns=("README.md",), exclude_patterns=())

    assert len(broken) == 1
    assert broken[0].file_path == "README.md"
    assert broken[0].line == 2
    assert broken[0].target == "docs/missing.md"


def test_check_docs_links_ignores_fenced_code(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(
        "```markdown\n[missing](docs/missing.md)\n```\n",
        encoding="utf-8",
    )

    assert check_docs_links(tmp_path, include_patterns=("README.md",), exclude_patterns=()) == []


def test_format_release_notes_renders_commit_table() -> None:
    notes = format_release_notes(
        [
            Commit(
                sha="abcdef123456",
                subject="Add docs link checks",
                author="copilot",
                date="2026-05-13",
            )
        ],
        since="v0.1.0-alpha",
        until="HEAD",
        generated_at=datetime(2026, 5, 13, tzinfo=UTC),
    )

    assert "# Release Notes Draft" in notes
    assert "- Range: `v0.1.0-alpha..HEAD`" in notes
    assert "| `abcdef1` | 2026-05-13 | Add docs link checks |" in notes


def test_ci_workflow_uploads_release_and_benchmark_artifacts() -> None:
    workflow = (Path(__file__).parents[1] / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert_action_major_at_least(workflow, "actions/upload-artifact", 4)
    assert "release-notes-draft.md" in workflow
    assert "geond-ci-benchmark" in workflow
    assert "benchmark-smoke.md" in workflow
    assert "benchmark-report.md" in workflow
    assert "geond benchmark-search" in workflow
    assert "python-package-dist" in workflow
    assert "dist/SHA256SUMS.txt" in workflow


def test_previous_tag_excludes_current_exact_tag(monkeypatch) -> None:
    calls: list[list[str]] = []

    class Completed:
        def __init__(self, stdout: str) -> None:
            self.stdout = stdout

    def fake_run(command, check, capture_output, text):
        calls.append(command)
        assert check is False
        assert capture_output is True
        assert text is True
        if "--exact-match" in command:
            return Completed("v0.2.0\n")
        return Completed("v0.1.0\n")

    monkeypatch.setattr(release_notes_module.subprocess, "run", fake_run)

    assert previous_tag("v0.2.0") == "v0.1.0"
    assert calls[1] == [
        "git",
        "describe",
        "--tags",
        "--abbrev=0",
        "--exclude",
        "v0.2.0",
        "v0.2.0",
    ]


def test_ci_workflow_creates_release_for_tags() -> None:
    workflow = (Path(__file__).parents[1] / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert "tags:" in workflow
    assert '"v*"' in workflow
    assert "--since-previous-tag" in workflow
    assert_action_major_at_least(workflow, "softprops/action-gh-release", 2)
    assert "body_path: release-notes-draft.md" in workflow
    assert "dist/*.tar.gz" in workflow
    assert "dist/*.whl" in workflow
    assert "dist/SHA256SUMS.txt" in workflow


def test_ci_workflow_signs_release_artifacts_with_sigstore() -> None:
    workflow = (Path(__file__).parents[1] / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert "id-token: write" in workflow
    assert "sigstore/gh-action-sigstore-python@v3.3.0" in workflow
    assert "upload-signing-artifacts: true" in workflow
    assert "release-signing-artifacts: false" in workflow
    assert "dist/*.sigstore.json" in workflow


def test_ci_workflow_has_optional_pypi_trusted_publishing() -> None:
    workflow = (Path(__file__).parents[1] / ".github" / "workflows" / "publish-pypi.yml").read_text(
        encoding="utf-8"
    )

    assert "workflow_dispatch:" in workflow
    assert "tag:" in workflow
    assert "if: startsWith(inputs.tag, 'v')" in workflow
    assert "ref: ${{ inputs.tag }}" in workflow
    assert "build-dist:" in workflow
    assert "publish-pypi:" in workflow
    assert "needs: build-dist" in workflow
    assert "pypa/gh-action-pypi-publish@release/v1" in workflow
    assert_action_major_at_least(workflow, "actions/upload-artifact", 4)
    assert_action_major_at_least(workflow, "actions/download-artifact", 4)
    assert "name: pypi-package-dist" in workflow
    assert "cp dist/*.tar.gz dist/*.whl publish-dist/" in workflow
    assert "packages-dir: publish-dist/" in workflow
    assert "print-hash: true" in workflow
    assert "id-token: write" in workflow


def test_write_dist_checksums_renders_sorted_sha256_lines(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    wheel = dist / "package-0.1-py3-none-any.whl"
    sdist = dist / "package-0.1.tar.gz"
    ignored = dist / "notes.txt"
    wheel.write_bytes(b"wheel")
    sdist.write_bytes(b"sdist")
    ignored.write_text("ignore", encoding="utf-8")

    entries = write_checksums(dist)

    assert [entry.path.name for entry in entries] == sorted([sdist.name, wheel.name])
    output = (dist / "SHA256SUMS.txt").read_text(encoding="utf-8")
    assert "package-0.1.tar.gz" in output
    assert "package-0.1-py3-none-any.whl" in output
    assert "notes.txt" not in output
    assert all(len(line.split("  ")[0]) == 64 for line in output.splitlines())
