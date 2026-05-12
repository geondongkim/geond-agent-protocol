from __future__ import annotations

from typing import Any

EVIDENCE_SCHEMA = "geond.evidence.v1"


def evidence_ref(
    kind: str,
    *,
    target_id: str | None = None,
    workspace_id: str | None = None,
    workspace_uri: str | None = None,
    source: str | None = None,
    locator: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    **aliases: Any,
) -> dict[str, Any]:
    ref: dict[str, Any] = {
        "schema": EVIDENCE_SCHEMA,
        "kind": kind,
        "locator": {},
        "metadata": {},
    }
    optional_fields = {
        "target_id": target_id,
        "workspace_id": workspace_id,
        "workspace_uri": workspace_uri,
        "source": source,
    }
    ref.update({key: value for key, value in optional_fields.items() if value is not None})
    if locator:
        ref["locator"] = {key: value for key, value in locator.items() if value is not None}
    if metadata:
        ref["metadata"] = {key: value for key, value in metadata.items() if value is not None}
    ref.update({key: value for key, value in aliases.items() if value is not None})
    return ref
