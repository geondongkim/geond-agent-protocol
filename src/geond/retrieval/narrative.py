"""Deterministic narrative summary generation over geond.evidence.v1 refs.

This module turns the structured evidence emitted by `explain_change` and
related retrieval calls into a short, human-readable narrative. It is
intentionally template-driven (no LLM call) so that:

- The output is reproducible across runs and easy to test.
- Privacy mode controls what is and is not included.
- Every sentence cites a concrete evidence ref via its target id.

The narrative is designed for two readers:

1. Another coding agent that needs a one-paragraph briefing before acting.
2. A human reviewer who wants to skim what an agent did without opening every
   evidence object.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from geond.config import Settings

PRIVACY_STRICT = {"strict", "local-only"}
EVIDENCE_SCHEMA = "geond.evidence.v1"


def _truncate(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _evidence_pointer(evidence: dict[str, Any] | None) -> str | None:
    if not evidence:
        return None
    kind = evidence.get("kind")
    target_id = evidence.get("target_id")
    if not kind or not target_id:
        return None
    return f"{kind}:{str(target_id)[:8]}"


def _format_pointers(pointers: Iterable[str | None]) -> str:
    cleaned = [pointer for pointer in pointers if pointer]
    if not cleaned:
        return ""
    return " [" + ", ".join(cleaned) + "]"


def summarize_explain_change(
    result: dict[str, Any],
    *,
    settings: Settings | None = None,
    include_message_snippets: bool | None = None,
    max_changesets: int = 3,
    max_entities: int = 5,
) -> dict[str, Any]:
    """Produce a narrative summary block for an `explain_change` result.

    The returned dict is intended to be merged into the original result under
    the `narrative` key. Callers may pass `include_message_snippets=False` to
    suppress raw chat content even when privacy mode would normally allow it.
    """

    privacy_mode = (settings.privacy_mode if settings else "redacted-cloud").lower()
    if include_message_snippets is None:
        include_message_snippets = privacy_mode not in PRIVACY_STRICT

    changesets = result.get("changesets") or []
    entities = result.get("touched_entities") or []
    messages = result.get("related_messages") or []
    snapshots = result.get("snapshots") or []
    file_path = result.get("file_path") or "the file"

    lines: list[str] = []
    citations: list[dict[str, Any]] = []

    if not changesets and not entities and not messages and not snapshots:
        return {
            "schema": EVIDENCE_SCHEMA + ".narrative",
            "headline": f"No evidence stored for {file_path} yet.",
            "lines": [],
            "citations": [],
            "privacy_mode": privacy_mode,
        }

    headline_parts = []
    if changesets:
        headline_parts.append(f"{len(changesets)} changeset(s)")
    if entities:
        headline_parts.append(f"{len(entities)} touched symbol(s)")
    if messages:
        headline_parts.append(f"{len(messages)} related chat message(s)")
    headline = (
        f"{file_path}: " + ", ".join(headline_parts) + "."
        if headline_parts
        else f"{file_path}: evidence available."
    )

    seen_changesets: set[str] = set()
    for changeset in changesets[:max_changesets]:
        changeset_id = changeset.get("changeset_id")
        if not changeset_id or changeset_id in seen_changesets:
            continue
        seen_changesets.add(changeset_id)
        intent = (changeset.get("intent") or "").strip()
        summary = (changeset.get("summary") or "").strip()
        git_commit = (changeset.get("git_commit") or "").strip()
        status = changeset.get("status")
        evidence = changeset.get("evidence")
        pointer = _evidence_pointer(evidence)
        if pointer:
            citations.append({"pointer": pointer, "evidence": evidence})

        descriptor = git_commit[:8] if git_commit else changeset_id[:8]
        body = summary or intent or "(no summary recorded)"
        suffix = f" — status={status}" if status else ""
        lines.append(
            f"Changeset {descriptor}: {_truncate(body)}{suffix}{_format_pointers([pointer])}"
        )

    if entities:
        kind_counts: dict[str, int] = {}
        entity_pointers: list[str | None] = []
        for entity in entities[:max_entities]:
            kind_counts[entity.get("kind") or "symbol"] = (
                kind_counts.get(entity.get("kind") or "symbol", 0) + 1
            )
            evidence = entity.get("evidence")
            pointer = _evidence_pointer(evidence)
            if pointer:
                citations.append({"pointer": pointer, "evidence": evidence})
                entity_pointers.append(pointer)
        kind_part = ", ".join(f"{count} {kind}" for kind, count in kind_counts.items())
        names = [
            entity.get("qualified_name") or entity.get("name") or "?"
            for entity in entities[:max_entities]
        ]
        lines.append(
            f"Touched symbols ({kind_part}): "
            + ", ".join(names)
            + _format_pointers(entity_pointers)
        )

    if include_message_snippets and messages:
        message_pointers: list[str | None] = []
        first = messages[0]
        snippet = first.get("snippet") or ""
        evidence = first.get("evidence")
        pointer = _evidence_pointer(evidence)
        if pointer:
            citations.append({"pointer": pointer, "evidence": evidence})
            message_pointers.append(pointer)
        if snippet:
            lines.append(
                f"Earliest related chat: {_truncate(snippet)}" + _format_pointers(message_pointers)
            )

    if snapshots:
        snapshot_pointers: list[str | None] = []
        for snapshot in snapshots[:1]:
            evidence = snapshot.get("evidence")
            pointer = _evidence_pointer(evidence)
            if pointer:
                citations.append({"pointer": pointer, "evidence": evidence})
                snapshot_pointers.append(pointer)
        if snapshot_pointers:
            lines.append(f"File snapshot recorded {_format_pointers(snapshot_pointers).lstrip()}")

    return {
        "schema": EVIDENCE_SCHEMA + ".narrative",
        "headline": headline,
        "lines": lines,
        "citations": citations,
        "privacy_mode": privacy_mode,
        "message_snippets_included": include_message_snippets,
    }


def summarize_changeset(
    changeset: dict[str, Any],
    *,
    settings: Settings | None = None,
    include_message_snippets: bool | None = None,
) -> dict[str, Any]:
    """Produce a narrative for a single changeset detail record.

    Expects a dict shaped like the output of `get_changeset_detail`.
    """

    privacy_mode = (settings.privacy_mode if settings else "redacted-cloud").lower()
    if include_message_snippets is None:
        include_message_snippets = privacy_mode not in PRIVACY_STRICT

    files = changeset.get("files") or []
    entities = changeset.get("touched_entities") or []
    git_commit = (changeset.get("git_commit") or "").strip()
    intent = (changeset.get("intent") or "").strip()
    summary = (changeset.get("summary") or "").strip()
    descriptor = git_commit[:8] if git_commit else (changeset.get("changeset_id") or "?")[:8]

    citations: list[dict[str, Any]] = []
    lines: list[str] = []

    evidence = changeset.get("evidence")
    pointer = _evidence_pointer(evidence)
    if pointer:
        citations.append({"pointer": pointer, "evidence": evidence})

    headline = f"Changeset {descriptor}: " + _truncate(summary or intent or "(no summary)")

    if files:
        file_pointers = []
        for file_row in files[:5]:
            file_evidence = file_row.get("evidence")
            file_pointer = _evidence_pointer(file_evidence)
            if file_pointer:
                citations.append({"pointer": file_pointer, "evidence": file_evidence})
                file_pointers.append(file_pointer)
        file_names = [row.get("file_path") for row in files[:5] if row.get("file_path")]
        if file_names:
            lines.append("Files: " + ", ".join(file_names) + _format_pointers(file_pointers))

    if entities:
        entity_pointers: list[str | None] = []
        for entity in entities[:5]:
            entity_evidence = entity.get("evidence")
            entity_pointer = _evidence_pointer(entity_evidence)
            if entity_pointer:
                citations.append({"pointer": entity_pointer, "evidence": entity_evidence})
                entity_pointers.append(entity_pointer)
        names = [
            entity.get("qualified_name") or entity.get("name") or "?" for entity in entities[:5]
        ]
        lines.append("Touched symbols: " + ", ".join(names) + _format_pointers(entity_pointers))

    return {
        "schema": EVIDENCE_SCHEMA + ".narrative",
        "headline": headline,
        "lines": lines,
        "citations": citations,
        "privacy_mode": privacy_mode,
        "message_snippets_included": include_message_snippets,
    }
