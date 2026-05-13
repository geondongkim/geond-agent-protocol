from __future__ import annotations

import argparse
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class Commit:
    sha: str
    subject: str
    author: str
    date: str

    @property
    def short_sha(self) -> str:
        return self.sha[:7]


def collect_commits(
    since: str | None = None,
    until: str = "HEAD",
    limit: int = 50,
) -> list[Commit]:
    command = [
        "git",
        "log",
        "--no-merges",
        f"--max-count={limit}",
        "--date=short",
        "--pretty=format:%H%x1f%s%x1f%an%x1f%ad",
    ]
    if since:
        command.append(f"{since}..{until}")
    else:
        command.append(until)
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    commits: list[Commit] = []
    for line in completed.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 4:
            continue
        commits.append(Commit(sha=parts[0], subject=parts[1], author=parts[2], date=parts[3]))
    return commits


def latest_tag() -> str | None:
    completed = subprocess.run(
        ["git", "describe", "--tags", "--abbrev=0"],
        check=False,
        capture_output=True,
        text=True,
    )
    tag = completed.stdout.strip()
    return tag or None


def format_release_notes(
    commits: list[Commit],
    *,
    title: str = "Release Notes Draft",
    since: str | None = None,
    until: str = "HEAD",
    generated_at: datetime | None = None,
) -> str:
    timestamp = (generated_at or datetime.now(UTC)).replace(microsecond=0).isoformat()
    range_label = f"{since}..{until}" if since else until
    lines = [
        f"# {title}",
        "",
        f"- Range: `{range_label}`",
        f"- Generated: `{timestamp}`",
        f"- Commits: `{len(commits)}`",
        "",
    ]
    if not commits:
        lines.append("No commits found for this range.")
        return "\n".join(lines) + "\n"

    lines.extend(
        [
            "| Commit | Date | Subject |",
            "| --- | --- | --- |",
        ]
    )
    for commit in commits:
        subject = commit.subject.replace("|", "\\|")
        lines.append(f"| `{commit.short_sha}` | {commit.date} | {subject} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate deterministic release notes markdown.")
    parser.add_argument(
        "--since",
        help="Start ref, exclusive. Defaults to latest tag when present.",
    )
    parser.add_argument("--until", default="HEAD", help="End ref, inclusive")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--title", default="Release Notes Draft")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    since = args.since if args.since is not None else latest_tag()
    notes = format_release_notes(
        collect_commits(since=since, until=args.until, limit=args.limit),
        title=args.title,
        since=since,
        until=args.until,
    )
    if args.output:
        args.output.write_text(notes, encoding="utf-8")
    else:
        print(notes, end="")


if __name__ == "__main__":
    main()
