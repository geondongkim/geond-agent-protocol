from __future__ import annotations

import argparse
import hashlib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATTERNS = ("*.tar.gz", "*.whl")


@dataclass(frozen=True)
class ChecksumEntry:
    path: Path
    sha256: str

    def line(self, root: Path) -> str:
        return f"{self.sha256}  {self.path.relative_to(root).as_posix()}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_dist_files(dist_dir: Path, patterns: tuple[str, ...] = DEFAULT_PATTERNS) -> list[Path]:
    files: dict[Path, None] = {}
    for pattern in patterns:
        for path in dist_dir.glob(pattern):
            if path.is_file():
                files[path] = None
    return sorted(files)


def write_checksums(
    dist_dir: Path,
    *,
    output: Path | None = None,
    patterns: tuple[str, ...] = DEFAULT_PATTERNS,
) -> list[ChecksumEntry]:
    dist_dir = dist_dir.resolve()
    output = output or dist_dir / "SHA256SUMS.txt"
    files = collect_dist_files(dist_dir, patterns)
    if not files:
        raise FileNotFoundError(f"No distribution files found in {dist_dir}")

    entries = [ChecksumEntry(path=path, sha256=sha256_file(path)) for path in files]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(entry.line(dist_dir) for entry in entries) + "\n", encoding="utf-8")
    return entries


def main() -> None:
    parser = argparse.ArgumentParser(description="Write SHA256 checksums for dist artifacts.")
    parser.add_argument("--dist-dir", type=Path, default=Path("dist"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    entries = write_checksums(args.dist_dir, output=args.output)
    print(f"Wrote {len(entries)} checksum entries")


if __name__ == "__main__":
    main()
