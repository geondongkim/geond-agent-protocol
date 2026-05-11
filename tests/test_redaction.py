from __future__ import annotations

from geond.redaction import REDACTION_MARKER, UNICODE_REPLACEMENT, redact_text, redact_value


def test_redact_text_masks_secret_assignments_without_removing_context() -> None:
    redacted, findings = redact_text(
        "run with GEOND_EMBEDDING_API_KEY=dummy-secret-value before indexing"
    )

    assert "dummy-secret-value" not in redacted
    assert f"GEOND_EMBEDDING_API_KEY={REDACTION_MARKER}" in redacted
    assert [finding.finding_type for finding in findings] == ["env_secret_assignment"]


def test_redact_value_masks_sensitive_keys_recursively() -> None:
    bearer_value = "dummy" + "BearerTokenValue12345"
    redacted, findings = redact_value(
        {
            "payload": {
                "api_key": "dummy-secret-value",
                "message": f"Bearer {bearer_value}",
            }
        }
    )

    assert redacted["payload"]["api_key"] == REDACTION_MARKER
    assert bearer_value not in redacted["payload"]["message"]
    assert {finding.finding_type for finding in findings} == {"sensitive_key", "bearer_token"}


def test_redact_text_replaces_invalid_surrogate_codepoints() -> None:
    redacted, findings = redact_text("정상 텍스트 \udceb 유지")

    assert redacted == f"정상 텍스트 {UNICODE_REPLACEMENT} 유지"
    assert [finding.finding_type for finding in findings] == ["unicode_replacement"]
