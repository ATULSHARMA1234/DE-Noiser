"""
Privacy-first redaction engine.

Scrub API keys, bearer tokens, emails, and PII from log messages before
they are sent to external embedding APIs or written to disk.
"""

from __future__ import annotations

import re

from denoiser.exceptions import RedactionError
from denoiser.logging import get_logger

logger = get_logger(__name__)

# Pre-compiled regex patterns for common secrets and PII.
# Order matters: we want to match specific high-value targets before generic ones.
_PATTERNS = {
    # JWTs typically start with eyJ and have three dot-separated base64url parts
    "JWT": re.compile(r"eyJ[a-zA-Z0-9_-]+\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+"),
    # Generic bearer tokens
    "BEARER_TOKEN": re.compile(r"(?i)(bearer\s+)[a-zA-Z0-9\-._~+/]+={0,2}"),
    # API Keys (AWS, GCP, generic)
    "AWS_ACCESS_KEY": re.compile(r"(?i)\b(AKIA|A3T|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{16}\b"),
    "GENERIC_API_KEY": re.compile(r"(?i)(api[_-]?key\s*[\"=:]\s*[\"']?)([a-zA-Z0-9\-_]{16,})([\"']?)"),
    "PASSWORD": re.compile(r"(?i)(password\s*[\"=:]\s*[\"']?)([^\\s,;\"']+)([\"']?)"),
    "SECRET": re.compile(r"(?i)(secret\s*[\"=:]\s*[\"']?)([^\\s,;\"']+)([\"']?)"),
    "TOKEN": re.compile(r"(?i)(token\s*[\"=:]\s*[\"']?)([^\\s,;\"']+)([\"']?)"),
    # Standard PII
    "EMAIL": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b"),
    # Basic Credit Card (14-16 digits, possibly separated by space/dash)
    "CREDIT_CARD": re.compile(r"\b(?:\d[ -]*?){13,16}\b"),
    # SSN (US)
    "SSN": re.compile(r"\b\d{3}[-.]?\d{2}[-.]?\d{4}\b"),
}


class Redactor:
    """Redacts sensitive information from text using pre-compiled regular expressions."""

    def __init__(self, enabled: bool = True) -> None:
        """
        Parameters
        ----------
        enabled : bool
            If False, the redact() method acts as a no-op, returning the original text.
        """
        self.enabled = enabled
        if self.enabled:
            logger.debug("Redactor initialized (ENABLED)")
        else:
            logger.debug("Redactor initialized (DISABLED)")

    def redact(self, text: str) -> str:
        """Apply all redaction rules to the input text.

        Parameters
        ----------
        text : str
            The input string to redact.

        Returns
        -------
        str
            The redacted string with sensitive values replaced by placeholders.
        """
        if not self.enabled or not text:
            return text

        try:
            for name, pattern in _PATTERNS.items():
                if name in ("GENERIC_API_KEY", "PASSWORD", "SECRET", "TOKEN"):
                    # Use capture groups to keep the key/label and quotes, replacing the value
                    text = pattern.sub(rf"\g<1><{name}>\g<3>", text)
                elif name == "BEARER_TOKEN":
                    text = pattern.sub(rf"\g<1><{name}>", text)
                else:
                    text = pattern.sub(f"<{name}>", text)
            return text
        except Exception as e:
            # Catching generic exception as regex operations on malformed strings
            # could raise unexpected errors (e.g., memory limits on catastrophic backtracking).
            logger.error("Failed to redact text", extra={"error": str(e), "text_preview": text[:50]})
            raise RedactionError(f"Redaction failed: {e}") from e
