from __future__ import annotations

from pathlib import Path
from typing import Any

from psycopg import Connection

from geond_orchestrator import (
    orchestrator_control,
    orchestrator_planner,
    orchestrator_task_planner,
)


def create_plan(
    conn: Connection,
    *,
    workspace_id_or_uri: str,
    run_id: str | None = None,
    agents: list[str] | None = None,
    limit: int = 50,
    base_dir: Path,
) -> dict[str, Any]:
    return orchestrator_planner.create_plan(
        conn,
        workspace_id_or_uri=workspace_id_or_uri,
        run_id=run_id,
        agents=agents,
        limit=limit,
        base_dir=base_dir,
        write_bundle=False,
    )


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


def propose_task_graph(
    conn: Connection,
    run_id: str,
    *,
    planner: str = "template",
    template: str = "auto",
    planner_agent: str = "codex",
    base_dir: Path,
) -> dict[str, Any]:
    return orchestrator_task_planner.propose_task_graph(
        conn,
        run_id,
        planner=planner,
        template=template,
        agent_name=planner_agent,
        execute_planner=False,
        base_dir=base_dir,
    )
