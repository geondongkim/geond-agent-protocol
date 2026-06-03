from __future__ import annotations

import json
from pathlib import Path

from geond.orchestration_manifest import write_run_manifest


def test_write_run_manifest_creates_stable_snapshot(tmp_path: Path) -> None:
    package = {
        "schema": "geond.run_handoff_package.v1",
        "status": "ok",
        "run": {
            "run_id": "run-1",
            "goal_id": "goal-1",
            "workspace_id": "workspace-1",
            "title": "Run title",
            "risk_level": "medium",
            "status": "active",
            "created_at": "2026-06-04T00:00:00+09:00",
        },
        "readiness": {
            "schema": "geond.readiness_report.v1",
            "status": "ready",
            "confidence": "medium",
            "recommended_action": "proceed",
            "blocking_reasons": [],
        },
        "workers": [{"worker_session_id": "worker-1"}],
        "command_evidence": [{"command_evidence_id": "cmd-1", "command": "uv run pytest"}],
        "approval_requests": [{"approval_id": "approval-1", "status": "approved"}],
        "review_findings": [{"finding_id": "finding-1", "status": "fixed", "summary": "fixed"}],
        "decisions": [{"decision_id": "decision-1", "status": "accepted", "decision": "ship"}],
    }

    first = write_run_manifest(package, "# Result\n", base_dir=tmp_path, write_result=True)
    second = write_run_manifest(package, "# Result\n", base_dir=tmp_path, write_result=True)

    assert first["status"] == "ok"
    assert first["files"] == second["files"]
    run_dir = tmp_path / "run-1"
    manifest = json.loads((run_dir / "EVIDENCE_MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == "geond.run_manifest.v1"
    assert manifest["run_id"] == "run-1"
    assert manifest["readiness_status"] == "ready"
    assert "COMMANDS.jsonl" in manifest["evidence_files"]
    assert (run_dir / "COMMANDS.jsonl").read_text(encoding="utf-8").count("\n") == 1
    assert (run_dir / "APPROVALS.jsonl").exists()
    assert (run_dir / "REVIEWS.md").exists()
    assert (run_dir / "DECISIONS.md").exists()
    assert (run_dir / "READINESS.md").exists()
    assert (run_dir / "RESULT.md").read_text(encoding="utf-8") == "# Result\n"
