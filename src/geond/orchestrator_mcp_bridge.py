from __future__ import annotations

from pathlib import Path
from typing import Any

from psycopg import Connection

from geond import orchestrator_control


def preview_agent_step(
    conn: Connection,
    run_id: str,
    *,
    agents: list[str] | None = None,
    max_workers: int = 1,
    model: str | None = None,
    sandbox: str = "workspace-write",
    timeout_seconds: int = 3600,
    base_dir: Path,
    limit: int = 50,
) -> dict[str, Any]:
    return orchestrator_control.preview_agent_step(
        conn,
        run_id,
        agents=agents,
        max_workers=max_workers,
        model=model,
        sandbox=sandbox,
        timeout_seconds=timeout_seconds,
        base_dir=base_dir,
        limit=limit,
    )
