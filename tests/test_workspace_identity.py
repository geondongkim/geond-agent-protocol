from __future__ import annotations

import hashlib

from geond.workspace_identity import (
    discover_workspace_fingerprints,
    normalize_git_remote,
    workspace_uri_from_path_or_uri,
)


def test_normalize_git_remote_strips_url_credentials() -> None:
    remote = normalize_git_remote("https://user:secret@example.com/org/repo.git/")

    assert remote == "https://example.com/org/repo.git"


def test_normalize_git_remote_keeps_scp_style_remote() -> None:
    remote = normalize_git_remote("git@example.com:org/repo.git")

    assert remote == "git@example.com:org/repo.git"


def test_workspace_uri_from_path_or_uri_preserves_uri() -> None:
    assert workspace_uri_from_path_or_uri("file:///tmp/project/") == "file:///tmp/project"


def test_discover_workspace_fingerprints_uses_manifest_hashes_without_git(tmp_path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "RealMe-OPIc"\n', encoding="utf-8")
    package_json = tmp_path / "package.json"
    package_json.write_text('{"name":"@geond/demo"}\n', encoding="utf-8")

    fingerprints = discover_workspace_fingerprints(str(tmp_path))
    by_type = {item["fingerprint_type"]: item for item in fingerprints}

    assert (
        by_type["file:sha256:pyproject.toml"]["fingerprint_value"]
        == hashlib.sha256(pyproject.read_bytes()).hexdigest()
    )
    assert (
        by_type["file:sha256:package.json"]["fingerprint_value"]
        == hashlib.sha256(package_json.read_bytes()).hexdigest()
    )
    assert (
        by_type["package:python:name-sha256"]["fingerprint_value"]
        == hashlib.sha256(b"realme-opic").hexdigest()
    )
    assert by_type["package:npm:name-sha256"]["metadata"]["name_hash_only"] is True
