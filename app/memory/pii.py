"""
PII / sensitive-data guard.

Single public function: contains_pii(text) -> bool.

Regex-based, deterministic, zero API cost.  Used as defence-in-depth alongside
the LLM extraction prompt — blocks structurally identifiable sensitive data from
being persisted to any memory store.

Scope: catches common *structured* PII patterns (card numbers, IBANs, SSNs,
passwords, IPs, etc.).  Semantic categories such as medical diagnoses are handled
by instructing the extraction LLM not to store them.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Each tuple: (label_for_logging, compiled_pattern)
# Labels are logged on a match — the matched value is never logged.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Payment card: 4×4 digit groups separated by space or dash
    (
        "payment_card",
        re.compile(r"\b\d{4}[- ]\d{4}[- ]\d{4}[- ]\d{4}\b"),
    ),
    # IBAN bank account (2-letter country code + 2 check digits + up to 30 alphanum)
    (
        "iban",
        re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b"),
    ),
    # US Social Security Number: NNN-NN-NNNN
    (
        "us_ssn",
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    ),
    # Norwegian fødselsnummer / personnummer: 6-digit birthdate + 5 digits
    (
        "no_personnummer",
        re.compile(r"\b\d{6}\s?\d{5}\b"),
    ),
    # Password / passord / passwort / passcode followed by a value
    (
        "password_literal",
        re.compile(
            r"(?i)\b(?:password|passord|passwort|passcode|passphrase)\s*[:=]\s*\S+"
        ),
    ),
    # Common API key / secret token prefixes
    (
        "api_key",
        re.compile(
            r"\b(?:sk-ant-|sk-[a-z]{2,5}-|ghp_|gho_|ghs_|xoxb-|bearer\s+)[a-z0-9_\-]{16,}\b",
            re.IGNORECASE,
        ),
    ),
    # IPv4 address (strict octet validation)
    (
        "ip_address",
        re.compile(
            r"\b(?:(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]\d|\d)\.){3}"
            r"(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]\d|\d)\b"
        ),
    ),
    # MAC address: six colon-separated hex octets
    (
        "mac_address",
        re.compile(r"\b[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}\b"),
    ),
    # PIN code in context ("pin: 1234", "pinkode = 5678", etc.)
    (
        "pin_in_context",
        re.compile(r"(?i)\bpin(?:\s*code|\s*kode)?\s*[:=]?\s*\d{4,8}\b"),
    ),
]


def contains_pii(text: str) -> bool:
    """
    Return True if *text* appears to contain PII or other sensitive data that
    must not be stored in memory.

    Logs the matched category at WARNING level — never the matched value itself.
    """
    for label, pattern in _PATTERNS:
        if pattern.search(text):
            logger.warning("PII guard: blocked — matched pattern '%s'", label)
            return True
    return False
