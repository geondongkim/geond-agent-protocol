from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

REDACTION_MARKER = "[REDACTED]"
UNICODE_REPLACEMENT = "\ufffd"


@dataclass(frozen=True)
class RedactionFinding:
    finding_type: str
    path: str
    action: str = "redacted"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TextPattern:
    finding_type: str
    regex: re.Pattern[str]
    replacement: str | None = None


TEXT_PATTERNS = (
    TextPattern(
        finding_type="env_secret_assignment",
        regex=re.compile(
            r"\b([A-Za-z_][A-Za-z0-9_]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|"
            r"CONNECTION[_-]?STRING|DATABASE[_-]?URL)\s*=\s*)([^\s\"']+)",
            re.IGNORECASE,
        ),
    ),
    TextPattern(
        finding_type="bearer_token",
        regex=re.compile(r"\b(Bearer\s+)([A-Za-z0-9._~+/=-]{16,})", re.IGNORECASE),
    ),
    TextPattern(
        finding_type="github_token",
        regex=re.compile(r"\b(gh[pousr]_[A-Za-z0-9_]{20,})\b"),
        replacement=REDACTION_MARKER,
    ),
    TextPattern(
        finding_type="openai_api_key",
        regex=re.compile(r"\b(sk-[A-Za-z0-9][A-Za-z0-9_-]{16,})\b"),
        replacement=REDACTION_MARKER,
    ),
    TextPattern(
        finding_type="url_password",
        regex=re.compile(r"(://[^:\s/@]+:)([^\s/@]+)(@)"),
    ),
)

SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "id_token",
    "auth_token",
    "authorization",
    "password",
    "passwd",
    "secret",
    "connection_string",
    "database_url",
    "client_secret",
    "private_key",
)


def redact_value(value: Any, path: str = "$") -> tuple[Any, list[RedactionFinding]]:
    if isinstance(value, str):
        return redact_text(value, path=path)
    if isinstance(value, list):
        redacted_items: list[Any] = []
        findings: list[RedactionFinding] = []
        for index, item in enumerate(value):
            redacted_item, item_findings = redact_value(item, path=f"{path}[{index}]")
            redacted_items.append(redacted_item)
            findings.extend(item_findings)
        return redacted_items, findings
    if isinstance(value, dict):
        redacted_dict: dict[str, Any] = {}
        findings: list[RedactionFinding] = []
        for key, item in value.items():
            safe_key, key_findings = redact_text(str(key), path=f"{path}.__key__")
            findings.extend(key_findings)
            child_path = f"{path}.{safe_key}"
            if is_sensitive_key(safe_key) and item not in (None, "", [], {}):
                redacted_dict[safe_key] = REDACTION_MARKER
                findings.append(
                    RedactionFinding(
                        finding_type="sensitive_key",
                        path=child_path,
                        metadata={"key": safe_key},
                    )
                )
                continue
            redacted_item, item_findings = redact_value(item, path=child_path)
            redacted_dict[safe_key] = redacted_item
            findings.extend(item_findings)
        return redacted_dict, findings
    return value, []


def redact_text(text: str, path: str = "$") -> tuple[str, list[RedactionFinding]]:
    redacted, replacement_count = sanitize_text(text)
    findings: list[RedactionFinding] = []
    if replacement_count:
        findings.append(
            RedactionFinding(
                finding_type="unicode_replacement",
                path=path,
                metadata={"count": replacement_count},
            )
        )
    for pattern in TEXT_PATTERNS:
        redacted, count = apply_pattern(pattern, redacted)
        if count:
            findings.append(
                RedactionFinding(
                    finding_type=pattern.finding_type,
                    path=path,
                    metadata={"count": count},
                )
            )
    return redacted, findings


def apply_pattern(pattern: TextPattern, text: str) -> tuple[str, int]:
    if pattern.replacement is not None:
        return pattern.regex.subn(pattern.replacement, text)

    if pattern.finding_type in {"env_secret_assignment", "bearer_token"}:
        return pattern.regex.subn(lambda match: f"{match.group(1)}{REDACTION_MARKER}", text)
    if pattern.finding_type == "url_password":
        return pattern.regex.subn(
            lambda match: f"{match.group(1)}{REDACTION_MARKER}{match.group(3)}",
            text,
        )
    return pattern.regex.subn(REDACTION_MARKER, text)


def is_sensitive_key(key: str) -> bool:
    normalized = key.replace("-", "_").lower()
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def sanitize_text(text: str) -> tuple[str, int]:
    replacement_count = 0
    characters: list[str] = []
    for character in text:
        codepoint = ord(character)
        if character == "\x00" or 0xD800 <= codepoint <= 0xDFFF:
            characters.append(UNICODE_REPLACEMENT)
            replacement_count += 1
        else:
            characters.append(character)
    return "".join(characters), replacement_count
