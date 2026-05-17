"""
Task 7: Unit tests for the PII Redactor.
"""

import pytest
from denoiser.preprocessing.redaction import Redactor


class TestRedactor:
    """Tests for the privacy-first PII redactor."""

    def test_redact_email(self):
        """Email addresses should be redacted."""
        redactor = Redactor(enabled=True)
        result = redactor.redact("Contact admin@company.com for help")
        assert "admin@company.com" not in result

    def test_redact_bearer_token(self):
        """Bearer tokens should be redacted."""
        redactor = Redactor(enabled=True)
        result = redactor.redact("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.test.sig")
        assert "eyJhbGciOiJIUzI1NiJ9" not in result

    def test_redact_api_key(self):
        """API keys should be redacted."""
        redactor = Redactor(enabled=True)
        result = redactor.redact("api_key=sk-1234567890abcdef")
        assert "sk-1234567890abcdef" not in result

    def test_redact_disabled(self):
        """When redaction is disabled, text should pass through unchanged."""
        redactor = Redactor(enabled=False)
        original = "secret_key=my_super_secret_value"
        result = redactor.redact(original)
        assert result == original

    def test_redact_preserves_normal_text(self):
        """Normal log text without PII should not be altered."""
        redactor = Redactor(enabled=True)
        original = "2026-01-01 INFO Application started successfully"
        result = redactor.redact(original)
        assert "Application started successfully" in result

    def test_redact_multiple_patterns(self):
        """Multiple PII patterns in one line should all be redacted."""
        redactor = Redactor(enabled=True)
        line = "User admin@test.com used token=abc123 from 192.168.1.1"
        result = redactor.redact(line)
        assert "admin@test.com" not in result
