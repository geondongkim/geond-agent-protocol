from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_NOTEBOOKS = (
    ROOT / "learn" / "01_local_shared_memory.ipynb",
    ROOT / "learn" / "02_handoffs_and_reservations.ipynb",
    ROOT / "learn" / "03_ai_pair_coding_workflow.ipynb",
    ROOT / "learn" / "04_shared_postgres_team_mode.ipynb",
)

REQUIRED_TERMS = (
    "objective",
    "prerequisites",
    "safety",
    "run",
    "expected outcome",
    "cleanup",
)
BLOCKED_PATTERNS = (
    re.compile(r"postgresql://[^<\s]+:[^<\s]+@", re.IGNORECASE),
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"C:\\Users\\[^<\s]+", re.IGNORECASE),
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate Geond tutorial notebooks.")
    parser.add_argument("notebooks", nargs="*", type=Path)
    args = parser.parse_args()

    notebooks = tuple(path.resolve() for path in args.notebooks) or DEFAULT_NOTEBOOKS
    errors: list[str] = []
    for notebook in notebooks:
        errors.extend(validate_notebook(notebook))

    if errors:
        for error in errors:
            print(error)
        raise SystemExit(1)
    print(f"Tutorial notebooks OK ({len(notebooks)} files)")


def validate_notebook(path: Path) -> list[str]:
    errors: list[str] = []
    if not path.exists():
        return [f"{path}: missing notebook"]

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"{path}: invalid JSON: {exc}"]

    if data.get("nbformat") != 4:
        errors.append(f"{path}: nbformat must be 4")

    cells = data.get("cells")
    if not isinstance(cells, list) or len(cells) < 6:
        errors.append(f"{path}: expected at least 6 cells")
        cells = []

    markdown_text = "\n".join(
        source_text(cell) for cell in cells if cell.get("cell_type") == "markdown"
    )
    code_cells = [cell for cell in cells if cell.get("cell_type") == "code"]
    lowered = markdown_text.lower()

    for term in REQUIRED_TERMS:
        if term not in lowered:
            errors.append(f"{path}: missing required tutorial term: {term}")

    if not code_cells:
        errors.append(f"{path}: expected at least one code cell")

    combined = "\n".join(source_text(cell) for cell in cells)
    for pattern in BLOCKED_PATTERNS:
        if pattern.search(combined):
            errors.append(f"{path}: blocked secret/private-path pattern matched {pattern.pattern}")

    return errors


def source_text(cell: dict[str, Any]) -> str:
    source = cell.get("source", "")
    if isinstance(source, list):
        return "".join(str(part) for part in source)
    return str(source)


if __name__ == "__main__":
    main()
