from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse, urlunparse

MANIFEST_FINGERPRINT_FILES = (
    "pyproject.toml",
    "package.json",
    "go.mod",
    "Cargo.toml",
    "uv.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
)


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
    root_path = Path(root_text) if root_text else path
    if root_path.is_file():
        root_path = root_path.parent

    remote = None
    first_commit = None
    if root_text:
        remote = normalize_git_remote(run_git(root_path, ["config", "--get", "remote.origin.url"]))
        first_commit = run_git(root_path, ["rev-list", "--max-parents=0", "HEAD"])
        if first_commit:
            first_commit = first_commit.splitlines()[0].strip()

    metadata = {
        "source": "workspace-discovery",
        "workspace_uri": workspace_uri_from_path_or_uri(str(root_path)),
    }
    fingerprints: list[dict[str, Any]] = []
    if remote:
        fingerprints.append(
            {
                "fingerprint_type": "git:remote",
                "fingerprint_value": remote,
                "metadata": {**metadata, "source": "git"},
            }
        )
    if first_commit:
        fingerprints.append(
            {
                "fingerprint_type": "git:first-commit",
                "fingerprint_value": first_commit,
                "metadata": {**metadata, "source": "git"},
            }
        )
    if remote and first_commit:
        fingerprints.append(
            {
                "fingerprint_type": "git:remote:first-commit",
                "fingerprint_value": f"{remote}#{first_commit}",
                "metadata": {**metadata, "source": "git"},
            }
        )
    fingerprints.extend(discover_manifest_fingerprints(root_path, metadata))
    return fingerprints


def discover_manifest_fingerprints(
    root_path: Path,
    metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    fingerprints: list[dict[str, Any]] = []
    for relative_name in MANIFEST_FINGERPRINT_FILES:
        manifest_path = root_path / relative_name
        digest = file_sha256(manifest_path)
        if not digest:
            continue
        file_metadata = {
            **metadata,
            "source": "manifest",
            "file_path": relative_name,
            "size_bytes": manifest_path.stat().st_size,
        }
        fingerprints.append(
            {
                "fingerprint_type": f"file:sha256:{relative_name}",
                "fingerprint_value": digest,
                "metadata": file_metadata,
            }
        )
        package_name = package_name_from_manifest(manifest_path)
        if package_name:
            fingerprints.append(
                {
                    "fingerprint_type": f"package:{package_ecosystem(relative_name)}:name-sha256",
                    "fingerprint_value": sha256_text(package_name.casefold()),
                    "metadata": {
                        **file_metadata,
                        "name_hash_only": True,
                        "name_length": len(package_name),
                    },
                }
            )
    return fingerprints


def file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def package_name_from_manifest(path: Path) -> str | None:
    if path.name == "package.json":
        return package_json_name(path)
    if path.name == "pyproject.toml":
        return pyproject_name(path)
    if path.name == "Cargo.toml":
        return cargo_name(path)
    if path.name == "go.mod":
        return go_module_name(path)
    return None


def package_ecosystem(file_name: str) -> str:
    if file_name == "package.json":
        return "npm"
    if file_name == "pyproject.toml":
        return "python"
    if file_name == "Cargo.toml":
        return "cargo"
    if file_name == "go.mod":
        return "go"
    return "manifest"


def package_json_name(path: Path) -> str | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    name = payload.get("name") if isinstance(payload, dict) else None
    return str(name).strip() if name else None


def pyproject_name(path: Path) -> str | None:
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    project = payload.get("project") if isinstance(payload, dict) else None
    if isinstance(project, dict) and project.get("name"):
        return str(project["name"]).strip()
    tool = payload.get("tool") if isinstance(payload, dict) else None
    poetry = tool.get("poetry") if isinstance(tool, dict) else None
    if isinstance(poetry, dict) and poetry.get("name"):
        return str(poetry["name"]).strip()
    return None


def cargo_name(path: Path) -> str | None:
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    package = payload.get("package") if isinstance(payload, dict) else None
    if isinstance(package, dict) and package.get("name"):
        return str(package["name"]).strip()
    return None


def go_module_name(path: Path) -> str | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("module "):
            return stripped.removeprefix("module ").strip() or None
    return None


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
