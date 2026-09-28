"""
Privacy-first redaction engine.

Scrub API keys, bearer tokens, emails, and PII from log messages before they are
embedded, written to disk, or indexed for search.

Two classes of rule live here, and the distinction matters operationally:

* **Secrets** (tokens, keys, passwords) are always redacted. There is no
  legitimate reason to keep a live credential in a log line, so these are not
  configurable.
* **Personal data** (IP addresses, phone numbers, IBANs, dates of birth,
  passport and national-insurance numbers, MAC addresses) is redacted according
  to a :class:`RedactionPolicy`. These are individually switchable because the
  right answer is jurisdictional: an IP address is personal data under GDPR, but
  masking it also removes the field an engineer needs to trace a network fault.
  The defaults are privacy-first (everything on); operators dial them back
  explicitly via ``SLD_REDACT_<CATEGORY>=false``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, fields
from enum import Enum

from denoiser.exceptions import RedactionError
from denoiser.logging import get_logger

logger = get_logger(__name__)


class _Strategy(Enum):
    """How a matched span is rewritten."""

    #: Replace the entire match with the placeholder.
    WHOLE = "whole"
    #: Keep group 1 (the label/prefix, e.g. ``password=``) and the optional
    #: closing quote in group 3; replace only the value in group 2.
    VALUE = "value"
    #: Like VALUE but the pattern has no trailing-quote group.
    VALUE_NO_SUFFIX = "value_no_suffix"
    #: Replace the whole match only if the digits pass a Luhn checksum.
    LUHN = "luhn"


@dataclass(frozen=True)
class _Rule:
    name: str
    pattern: re.Pattern[str]
    strategy: _Strategy


def _luhn_ok(digits: str) -> bool:
    """Standard Luhn (mod-10) checksum, as used by every major card scheme.

    Without this, any 13-19 digit run — order ids, byte counters, Kubernetes
    uids, build numbers — is indistinguishable from a card number, and gets
    silently destroyed in logs an engineer is trying to read.
    """
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = ord(ch) - 48
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


# ── Secrets: always redacted ────────────────────────────────────────────────
#
# Ordered most-specific first, so a recognisable vendor credential is labelled
# as such before a generic `token=` rule can claim part of it.
#
# NOTE on the value character class below: it is `[^\s,;"']+`, with a single
# backslash. An earlier revision used `[^\\s,;"']+`, which inside a character
# class means "not a literal backslash, and not a literal s" — so any value
# containing the letter s was truncated at that letter or missed entirely
# (`password="superSecret123"` was left completely intact). Keep it single.
_VALUE = r"([^\s,;\"']+)"

_SECRET_RULES: list[_Rule] = [
    # PEM private key blocks. Matched to the END marker when present, otherwise
    # to the end of the line — logs frequently carry a truncated header only.
    _Rule("PRIVATE_KEY", re.compile(
        r"-----BEGIN[A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END[A-Z ]*PRIVATE KEY-----|$)",
        re.MULTILINE,
    ), _Strategy.WHOLE),
    # JWTs: three dot-separated base64url segments starting with the `{"` header.
    _Rule("JWT", re.compile(
        r"eyJ[a-zA-Z0-9_-]+\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+"
    ), _Strategy.WHOLE),
    # GitHub personal-access / app / refresh tokens.
    _Rule("GITHUB_TOKEN", re.compile(
        r"\bgh[pousr]_[A-Za-z0-9]{20,255}\b"
    ), _Strategy.WHOLE),
    # Slack bot/user/app/refresh tokens.
    _Rule("SLACK_TOKEN", re.compile(
        r"\bxox[baprse]-[A-Za-z0-9-]{10,}"
    ), _Strategy.WHOLE),
    # Stripe secret/publishable/restricted keys.
    _Rule("STRIPE_KEY", re.compile(
        r"\b[sprk]k_(?:live|test)_[A-Za-z0-9]{10,}\b"
    ), _Strategy.WHOLE),
    _Rule("AWS_ACCESS_KEY", re.compile(
        r"(?i)\b(?:AKIA|A3T|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{16}\b"
    ), _Strategy.WHOLE),
    # The 40-char secret half of an AWS key pair. Labelled, because the raw value
    # is ordinary base64 and unmatchable on shape alone.
    _Rule("AWS_SECRET_KEY", re.compile(
        r"(?i)(aws_secret_access_key\s*[\"=:]\s*[\"']?)([A-Za-z0-9/+=]{20,})"
    ), _Strategy.VALUE_NO_SUFFIX),
    _Rule("BEARER_TOKEN", re.compile(
        r"(?i)(bearer\s+)[a-zA-Z0-9\-._~+/]+={0,2}"
    ), _Strategy.VALUE_NO_SUFFIX),
    # HTTP Basic credentials — base64 of `user:password`.
    _Rule("BASIC_AUTH", re.compile(
        r"(?i)(basic\s+)[A-Za-z0-9+/]{8,}={0,2}"
    ), _Strategy.VALUE_NO_SUFFIX),
    # Credentials embedded in a connection URI: scheme://user:password@host
    _Rule("URI_PASSWORD", re.compile(
        r"([a-zA-Z][a-zA-Z0-9+.\-]*://[^:/?#\s]+:)([^@/?#\s]+)(@)"
    ), _Strategy.VALUE),
    _Rule("GENERIC_API_KEY", re.compile(
        r"(?i)(api[_-]?key\s*[\"=:]\s*[\"']?)([a-zA-Z0-9\-_]{16,})([\"']?)"
    ), _Strategy.VALUE),
    _Rule("PASSWORD", re.compile(
        rf"(?i)(password\s*[\"=:]\s*[\"']?){_VALUE}([\"']?)"
    ), _Strategy.VALUE),
    _Rule("SECRET", re.compile(
        rf"(?i)(secret\s*[\"=:]\s*[\"']?){_VALUE}([\"']?)"
    ), _Strategy.VALUE),
    _Rule("TOKEN", re.compile(
        rf"(?i)(token\s*[\"=:]\s*[\"']?){_VALUE}([\"']?)"
    ), _Strategy.VALUE),
    # Standard PII that is never optional.
    _Rule("EMAIL", re.compile(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,7}\b"
    ), _Strategy.WHOLE),
    # Card numbers, gated on three independent tests: a plausible issuer prefix
    # (2-6 covers Mastercard/Amex/Visa/Discover), a real card *grouping*, and a
    # Luhn checksum. All three are needed — a permissive digit-run rule with
    # only Luhn still swallowed things like "buckets 1 2 3 4 5 6 7 8 9 10 11 12
    # 13", where an arbitrary sequence happens to satisfy the checksum one time
    # in ten. Accepting only contiguous digits or 4/6-digit groups excludes runs
    # of small numbers outright.
    _Rule("CREDIT_CARD", re.compile(
        r"\b(?:"
        r"[2-6]\d{12,18}"                      # contiguous 13-19 digits
        r"|[2-6]\d{3}(?:[ -]\d{4}){2,4}"       # 4-4-4-4 / 4-4-4-4-4
        r"|[2-6]\d{3}[ -]\d{6}[ -]\d{5}"       # Amex 4-6-5
        r")\b"
    ), _Strategy.LUHN),
    _Rule("SSN", re.compile(
        r"\b\d{3}[-.]?\d{2}[-.]?\d{4}\b"
    ), _Strategy.WHOLE),
]


# ── Personal data: individually switchable ──────────────────────────────────
#
# Each entry is keyed by the RedactionPolicy field that governs it.
_PII_RULES: dict[str, list[_Rule]] = {
    # Declared before redact_ip: a MAC address is six colon-separated hex pairs
    # and a loose IPv6 rule will happily claim it, mislabelling the match.
    "redact_mac": [
        _Rule("MAC_ADDRESS", re.compile(
            r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b"
        ), _Strategy.WHOLE),
    ],
    # Known limitation: a four-part version string ("1.2.3.4") is
    # indistinguishable from a dotted quad, and will be masked when this
    # category is on. That ambiguity is unresolvable by pattern alone and is
    # part of why the category is switchable.
    "redact_ip": [
        _Rule("IPV4", re.compile(
            r"\b(?:(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)\.){3}"
            r"(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)\b"
        ), _Strategy.WHOLE),
        # Full RFC 4291 form plus every `::` compression. Written out branch by
        # branch rather than as one loose expression so that ordinary
        # colon-separated text — clock times, MAC addresses, host:port pairs —
        # cannot match: every compressed branch requires a literal `::`, and the
        # uncompressed branch requires all eight groups.
        _Rule("IPV6", re.compile(
            r"(?<![:.\w])(?:"
            r"(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}"
            r"|(?:[0-9A-Fa-f]{1,4}:){1,7}:"
            r"|(?:[0-9A-Fa-f]{1,4}:){1,6}:[0-9A-Fa-f]{1,4}"
            r"|(?:[0-9A-Fa-f]{1,4}:){1,5}(?::[0-9A-Fa-f]{1,4}){1,2}"
            r"|(?:[0-9A-Fa-f]{1,4}:){1,4}(?::[0-9A-Fa-f]{1,4}){1,3}"
            r"|(?:[0-9A-Fa-f]{1,4}:){1,3}(?::[0-9A-Fa-f]{1,4}){1,4}"
            r"|(?:[0-9A-Fa-f]{1,4}:){1,2}(?::[0-9A-Fa-f]{1,4}){1,5}"
            r"|[0-9A-Fa-f]{1,4}:(?::[0-9A-Fa-f]{1,4}){1,6}"
            r"|:(?::[0-9A-Fa-f]{1,4}){1,7}"
            r"|::"
            r")(?![:.\w])"
        ), _Strategy.WHOLE),
    ],
    "redact_phone": [
        # E.164 only. Requiring the leading `+` keeps ordinary numbers in logs
        # (ports, counts, durations) from being mistaken for phone numbers.
        _Rule("PHONE", re.compile(
            r"\+\d{1,3}[-.\s]?\(?\d{1,4}\)?(?:[-.\s]?\d{2,4}){2,3}\b"
        ), _Strategy.WHOLE),
    ],
    "redact_iban": [
        _Rule("IBAN", re.compile(
            r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"
        ), _Strategy.WHOLE),
    ],
    "redact_dob": [
        # Label-anchored on purpose. A bare YYYY-MM-DD matches the date half of
        # essentially every log timestamp; an unanchored rule would redact the
        # clock out of the entire corpus.
        _Rule("DOB", re.compile(
            r"(?i)((?:dob|date_?of_?birth|birth_?date)\s*[\"=:]\s*[\"']?)"
            r"(\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{4})"
        ), _Strategy.VALUE_NO_SUFFIX),
    ],
    "redact_passport": [
        _Rule("PASSPORT", re.compile(
            r"(?i)(passport(?:_?(?:no|number))?\s*[\"=:]\s*[\"']?)([A-Z0-9]{6,12})"
        ), _Strategy.VALUE_NO_SUFFIX),
    ],
    "redact_national_id": [
        # UK National Insurance number. The official prefix rules (no D, F, I,
        # Q, U or V in first position; additionally no O in second) make this
        # specific enough to match without a label.
        _Rule("NATIONAL_ID", re.compile(
            r"\b[A-CEGHJ-PR-TW-Z][A-CEGHJ-NPR-TW-Z]\d{6}[A-D]\b"
        ), _Strategy.WHOLE),
    ],
}


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class RedactionPolicy:
    """Which categories of personal data to mask.

    Defaults are privacy-first: everything on. Each field reads
    ``SLD_REDACT_<FIELD>`` from the environment when constructed via
    :meth:`from_env`, so a deployment can keep IP addresses readable for network
    debugging without editing code.
    """

    redact_ip: bool = True
    redact_phone: bool = True
    redact_iban: bool = True
    redact_dob: bool = True
    redact_passport: bool = True
    redact_national_id: bool = True
    redact_mac: bool = True

    @classmethod
    def from_env(cls) -> RedactionPolicy:
        return cls(**{
            f.name: _env_flag(f"SLD_{f.name.upper()}", bool(f.default))
            for f in fields(cls)
        })

    @classmethod
    def all_disabled(cls) -> RedactionPolicy:
        return cls(**{f.name: False for f in fields(cls)})


class Redactor:
    """Redacts secrets and personal data from text using compiled regexes."""

    def __init__(
        self,
        enabled: bool = True,
        policy: RedactionPolicy | None = None,
    ) -> None:
        """
        Parameters
        ----------
        enabled : bool
            If False, :meth:`redact` is a no-op returning the original text.
        policy : RedactionPolicy | None
            Which personal-data categories to mask. Defaults to the
            environment-derived policy.
        """
        self.enabled = enabled
        self.policy = policy or RedactionPolicy.from_env()
        self._rules = self._build_rules()
        logger.debug(
            "Redactor initialized",
            extra={"enabled": enabled, "rules": len(self._rules)},
        )

    def _build_rules(self) -> list[_Rule]:
        """Secrets, then whichever personal-data categories the policy enables.

        Disabled categories are left out of the list entirely rather than
        skipped per call, so a disabled rule costs nothing at runtime.
        """
        rules = list(_SECRET_RULES)
        for flag, pii_rules in _PII_RULES.items():
            if getattr(self.policy, flag, False):
                rules.extend(pii_rules)
        return rules

    @staticmethod
    def _apply(rule: _Rule, text: str) -> str:
        placeholder = f"<{rule.name}>"

        if rule.strategy is _Strategy.WHOLE:
            return rule.pattern.sub(placeholder, text)

        if rule.strategy is _Strategy.VALUE:
            return rule.pattern.sub(rf"\g<1>{placeholder}\g<3>", text)

        if rule.strategy is _Strategy.VALUE_NO_SUFFIX:
            return rule.pattern.sub(rf"\g<1>{placeholder}", text)

        if rule.strategy is _Strategy.LUHN:
            def _sub(match: re.Match[str]) -> str:
                digits = re.sub(r"\D", "", match.group(0))
                return placeholder if _luhn_ok(digits) else match.group(0)

            return rule.pattern.sub(_sub, text)

        return text

    def redact(self, text: str) -> str:
        """Apply every active rule to ``text``.

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
            for rule in self._rules:
                text = self._apply(rule, text)
            return text
        except Exception as e:
            # Regex work on adversarial input can fail in ways that are not worth
            # enumerating (catastrophic backtracking, memory limits). Failing
            # closed matters here: the caller must not receive un-redacted text.
            logger.error("Failed to redact text", extra={"error": str(e), "text_preview": text[:50]})
            raise RedactionError(f"Redaction failed: {e}") from e

    def redact_batch(self, texts: list[str]) -> list[str]:
        """Redact a list of strings. Convenience for the ingestion hot path."""
        return [self.redact(t) for t in texts]


def redact_value(value: object, redactor: Redactor) -> object:
    """Recursively redact strings inside a JSON-shaped structure.

    Log payloads arrive as nested dicts; redacting only the top-level
    ``message`` would leave PII sitting in sibling fields.
    """
    if isinstance(value, str):
        return redactor.redact(value)
    if isinstance(value, dict):
        return {k: redact_value(v, redactor) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(v, redactor) for v in value]
    return value
