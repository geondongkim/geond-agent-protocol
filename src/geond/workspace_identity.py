from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse, urlunparse


def workspace_uri_from_path_or_uri(path_or_uri: str) -> str:
    value = path_or_uri.strip().replace("\\", "/")
    if "://" in value:
        return value.rstrip("/")

    path = Path(path_or_uri).expanduser()
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path.absolute()
    normalized = resolved.as_posix()
    if re.match(r"^[A-Za-z]:/", normalized):
        return f"file:///{normalized}"
    if normalized.startswith("/"):
        return f"file://{normalized}"
    return f"file:///{normalized}"


def local_path_from_path_or_uri(path_or_uri: str) -> Path | None:
    value = path_or_uri.strip()
    if not value:
        return None
    if "://" not in value:
        return Path(value).expanduser()

    parsed = urlparse(value)
    if parsed.scheme != "file":
        return None
    path_text = unquote(parsed.path)
    if re.match(r"^/[A-Za-z]:/", path_text):
        path_text = path_text[1:]
    if parsed.netloc:
        path_text = f"//{parsed.netloc}{path_text}"
    return Path(path_text)


def discover_workspace_fingerprints(path_or_uri: str) -> list[dict[str, Any]]:
    path = local_path_from_path_or_uri(path_or_uri)
    if path is None:
        return []

    root_text = run_git(path, ["rev-parse", "--show-toplevel"])
    if not root_text:
        return []

    root_path = Path(root_text)
    remote = normalize_git_remote(run_git(root_path, ["config", "--get", "remote.origin.url"]))
    first_commit = run_git(root_path, ["rev-list", "--max-parents=0", "HEAD"])
    if first_commit:
        first_commit = first_commit.splitlines()[0].strip()

    metadata = {
        "source": "git",
        "workspace_uri": workspace_uri_from_path_or_uri(str(root_path)),
    }
    fingerprints: list[dict[str, Any]] = []
    if remote:
        fingerprints.append(
            {
                "fingerprint_type": "git:remote",
                "fingerprint_value": remote,
                "metadata": metadata,
            }
        )
    if first_commit:
        fingerprints.append(
            {
                "fingerprint_type": "git:first-commit",
                "fingerprint_value": first_commit,
                "metadata": metadata,
            }
        )
    if remote and first_commit:
        fingerprints.append(
            {
                "fingerprint_type": "git:remote:first-commit",
                "fingerprint_value": f"{remote}#{first_commit}",
                "metadata": metadata,
            }
        )
    return fingerprints


def run_git(cwd: Path, args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def normalize_git_remote(remote: str | None) -> str | None:
    if not remote:
        return None
    value = remote.strip().rstrip("/")
    if "://" not in value:
        return value

    parsed = urlparse(value)
    hostname = parsed.hostname
    if not hostname:
        return value
    netloc = hostname
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    return urlunparse((parsed.scheme, netloc, parsed.path.rstrip("/"), "", "", ""))
