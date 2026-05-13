from __future__ import annotations

import argparse
import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

DEFAULT_INCLUDE_PATTERNS = (
    "README.md",
    "docs/*.md",
    "docs/**/*.md",
    "examples/*.md",
    "examples/**/*.md",
)
DEFAULT_EXCLUDE_PATTERNS = (
    ".git/**",
    ".venv/**",
    "build/**",
    "dist/**",
    "docs/patent/**",
    "repo/**",
    "**/__pycache__/**",
)
LINK_RE = re.compile(r"!?\[[^\]\n]+\]\(([^)\n]+)\)")
SKIPPED_SCHEMES = {"http", "https", "mailto", "data", "geond", "vscode", "command"}


@dataclass(frozen=True)
class BrokenLink:
    file_path: str
    line: int
    target: str
    reason: str


def check_docs_links(
    root: Path,
    include_patterns: tuple[str, ...] = DEFAULT_INCLUDE_PATTERNS,
    exclude_patterns: tuple[str, ...] = DEFAULT_EXCLUDE_PATTERNS,
) -> list[BrokenLink]:
    root = root.resolve()
    broken: list[BrokenLink] = []
    for markdown_file in iter_markdown_files(root, include_patterns, exclude_patterns):
        text = strip_fenced_code(markdown_file.read_text(encoding="utf-8"))
        rel_file = markdown_file.relative_to(root).as_posix()
        for match in LINK_RE.finditer(text):
            raw_target = match.group(1).strip()
            target = normalize_link_target(raw_target)
            if not target or should_skip_target(target):
                continue
            path_part = target.split("#", 1)[0].split("?", 1)[0]
            if not path_part:
                continue
            candidate = resolve_target(root, markdown_file, path_part)
            if candidate is None:
                broken.append(
                    BrokenLink(
                        rel_file,
                        line_number(text, match.start()),
                        raw_target,
                        "outside root",
                    )
                )
            elif not candidate.exists():
                broken.append(
                    BrokenLink(
                        rel_file,
                        line_number(text, match.start()),
                        raw_target,
                        "missing file",
                    )
                )
    return broken


def iter_markdown_files(
    root: Path,
    include_patterns: tuple[str, ...],
    exclude_patterns: tuple[str, ...],
) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*.md"):
        rel = path.relative_to(root).as_posix()
        if matches_any(rel, exclude_patterns):
            continue
        if matches_any(rel, include_patterns):
            files.append(path)
    return sorted(files)


def matches_any(value: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(value, pattern) for pattern in patterns)


def strip_fenced_code(text: str) -> str:
    output: list[str] = []
    in_fence = False
    fence_marker = ""
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
            output.append("\n")
        elif in_fence:
            output.append("\n")
        else:
            output.append(line)
    return "".join(output)


def normalize_link_target(raw_target: str) -> str:
    target = raw_target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1].strip()
    if " " in target:
        target = target.split(" ", 1)[0].strip()
    return target


def should_skip_target(target: str) -> bool:
    parsed = urlparse(target)
    return bool(parsed.scheme and parsed.scheme.lower() in SKIPPED_SCHEMES)


def resolve_target(root: Path, markdown_file: Path, path_part: str) -> Path | None:
    normalized = unquote(path_part).replace("\\", "/")
    base = root if normalized.startswith("/") else markdown_file.parent
    candidate = (base / normalized.lstrip("/")).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate local Markdown links.")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--include", action="append", help="Glob to include; can repeat")
    parser.add_argument("--exclude", action="append", help="Glob to exclude; can repeat")
    args = parser.parse_args()

    include_patterns = tuple(args.include or DEFAULT_INCLUDE_PATTERNS)
    exclude_patterns = tuple([*DEFAULT_EXCLUDE_PATTERNS, *(args.exclude or [])])
    broken = check_docs_links(args.root, include_patterns, exclude_patterns)
    if broken:
        for item in broken:
            print(f"{item.file_path}:{item.line}: {item.target} ({item.reason})")
        raise SystemExit(1)
    print("Markdown links OK")


if __name__ == "__main__":
    main()
