from __future__ import annotations

from geond.workspace_identity import normalize_git_remote, workspace_uri_from_path_or_uri


def test_normalize_git_remote_strips_url_credentials() -> None:
    remote = normalize_git_remote("https://user:secret@example.com/org/repo.git/")

    assert remote == "https://example.com/org/repo.git"


def test_normalize_git_remote_keeps_scp_style_remote() -> None:
    remote = normalize_git_remote("git@example.com:org/repo.git")

    assert remote == "git@example.com:org/repo.git"


def test_workspace_uri_from_path_or_uri_preserves_uri() -> None:
    assert workspace_uri_from_path_or_uri("file:///tmp/project/") == "file:///tmp/project"
