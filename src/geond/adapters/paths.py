from __future__ import annotations

from pathlib import Path


def newest_first_key(path: Path) -> tuple[float, str]:
    return (-path.stat().st_mtime, str(path))
