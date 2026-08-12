"""
Task 7: Unit tests for the PII Redactor.
"""

from denoiser.preprocessing.redaction import RedactionPolicy, Redactor


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


class TestSecretPatternCoverage:
    """T-10/T-11: values the engine previously let through in full."""

    def test_password_containing_the_letter_s_is_fully_masked(self):
        """Regression: `[^\\\\s...]` excluded the literal letter 's', so any
        value containing one was truncated at it or missed entirely."""
        redactor = Redactor(enabled=True)
        result = redactor.redact('password="superSecret123"')
        assert "superSecret123" not in result
        assert "uperSecret123" not in result
        assert "<PASSWORD>" in result

    def test_token_containing_s_is_fully_masked(self):
        redactor = Redactor(enabled=True)
        result = redactor.redact("token=asdf1234")
        assert "sdf1234" not in result

    def test_secret_containing_s_is_fully_masked(self):
        redactor = Redactor(enabled=True)
        result = redactor.redact("client_secret: sk_live_abc")
        assert "sk_live_abc" not in result

    def test_github_token(self):
        redactor = Redactor(enabled=True)
        secret = "ghp_16C7e42F292c6912E7710c838347Ae178B4a"
        assert secret not in redactor.redact(f"clone failed {secret}")

    def test_slack_token(self):
        redactor = Redactor(enabled=True)
        secret = "xoxb-2411-2411-JHnO0V0dRRBcYWNlciBz"
        assert secret not in redactor.redact(f"posting to slack {secret}")

    def test_stripe_live_key(self):
        redactor = Redactor(enabled=True)
        # Assembled at runtime: as one literal this synthetic vector is
        # indistinguishable from a real key to GitHub's push protection.
        secret = "sk_" + "live_" + "51H8ZqEJk3mNpQrStUvWxYz012345"
        assert secret not in redactor.redact(f"charge failed {secret}")

    def test_pem_private_key_block(self):
        redactor = Redactor(enabled=True)
        result = redactor.redact("-----BEGIN RSA PRIVATE KEY-----MIIEowIBAAKCAQEA")
        assert "MIIEow" not in result

    def test_aws_secret_access_key(self):
        redactor = Redactor(enabled=True)
        secret = "wJalrXUtnFEMI/K7MDENG/bPxRfiCY"
        assert secret not in redactor.redact(f"aws_secret_access_key={secret}")

    def test_http_basic_credentials(self):
        redactor = Redactor(enabled=True)
        secret = "YWRtaW46cGFzc3dvcmQ="
        assert secret not in redactor.redact(f"Authorization: Basic {secret}")

    def test_password_embedded_in_connection_uri(self):
        redactor = Redactor(enabled=True)
        result = redactor.redact("postgres://admin:P@ssw0rd@db.acme:5432/prod")
        assert "P@ssw0rd" not in result


class TestPersonalDataPolicy:
    """T-12: each category masks when enabled and is inert when disabled."""

    CASES = [
        ("redact_ip", "client_ip=203.0.113.42", "203.0.113.42"),
        ("redact_ip", "client=2001:db8::8a2e:370:7334", "2001:db8::8a2e:370:7334"),
        ("redact_phone", "contact +1-415-555-0198", "+1-415-555-0198"),
        ("redact_iban", "IBAN DE89370400440532013000 sent", "DE89370400440532013000"),
        ("redact_dob", "dob=1985-04-12 patient", "1985-04-12"),
        ("redact_passport", "passport=X12345678", "X12345678"),
        ("redact_national_id", "NI AB123456C", "AB123456C"),
        ("redact_mac", "device 00:1B:44:11:3A:B7", "00:1B:44:11:3A:B7"),
    ]

    def test_each_category_masks_when_enabled(self):
        redactor = Redactor(enabled=True, policy=RedactionPolicy())
        for _flag, text, sensitive in self.CASES:
            assert sensitive not in redactor.redact(text), f"{sensitive} survived"

    def test_each_category_is_inert_when_disabled(self):
        redactor = Redactor(enabled=True, policy=RedactionPolicy.all_disabled())
        for _flag, text, sensitive in self.CASES:
            assert sensitive in redactor.redact(text), f"{sensitive} masked while disabled"

    def test_disabling_personal_data_does_not_disable_secrets(self):
        redactor = Redactor(enabled=True, policy=RedactionPolicy.all_disabled())
        result = redactor.redact("password=hunter2 ghp_16C7e42F292c6912E7710c838347Ae178B4a")
        assert "hunter2" not in result
        assert "ghp_16C7e42F292c6912E7710c838347Ae178B4a" not in result

    def test_single_category_can_be_disabled_independently(self):
        redactor = Redactor(enabled=True, policy=RedactionPolicy(redact_ip=False))
        result = redactor.redact("ip=203.0.113.42 mac=00:1B:44:11:3A:B7")
        assert "203.0.113.42" in result
        assert "00:1B:44:11:3A:B7" not in result


class TestCreditCardLuhn:
    """T-13: only real card numbers are masked."""

    def test_valid_cards_are_masked(self):
        redactor = Redactor(enabled=True)
        for pan in (
            "4111111111111111",
            "4111 1111 1111 1111",
            "4111-1111-1111-1111",
            "5500005555555559",
            "378282246310005",
            "3782 822463 10005",
            "6011111111111117",
        ):
            assert pan not in redactor.redact(f"charged {pan}"), pan

    def test_long_integers_that_are_not_cards_survive(self):
        """These were all destroyed before the Luhn + issuer-prefix gate."""
        redactor = Redactor(enabled=True)
        for text in (
            "order_id=1234567890123456 shipped",
            "transferred 9876543210987 bytes",
            "build 2026072612345678 deployed",
            "span start 1785068722410270200 end",
            "pod uid 1234-5678-9012-3456 restarted",
            # A run of small numbers can satisfy Luhn by coincidence; the
            # grouping rule is what keeps it out.
            "p50=1 p90=22 p99=333 p999=4444 buckets 1 2 3 4 5 6 7 8 9 10 11 12 13",
        ):
            assert redactor.redact(text) == text, text

    def test_clock_and_host_port_are_not_mistaken_for_addresses(self):
        redactor = Redactor(enabled=True)
        for text in ("2026-07-26T10:15:00Z ERROR svc", "upstream db.acme:5432 timeout"):
            assert redactor.redact(text) == text
