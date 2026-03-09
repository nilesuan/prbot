"""Secret and PII redaction (story-5-4, story-6-4).

Output-side safety net: scans final comment text for credential
patterns and PII before posting. Replaces matches with [REDACTED].
"""

from __future__ import annotations

import re

# Secret patterns — compiled regexes for credential detection
SECRET_PATTERNS: list[re.Pattern[str]] = [
    # AWS Access Key ID (always starts with AKIA)
    re.compile(r"AKIA[0-9A-Z]{16}"),
    # AWS Secret Key — narrowed per GAP-8: requires aws_secret label context
    re.compile(
        r"(?i)(?:aws_secret_access_key|secret_access_key|aws_secret)"
        r"[\s=:\"']+([A-Za-z0-9/+=]{40})",
    ),
    # GitHub Classic PAT (ghp_)
    re.compile(r"ghp_[A-Za-z0-9]{36,}"),
    # GitHub Fine-grained PAT (github_pat_)
    re.compile(r"github_pat_[A-Za-z0-9_]{22,}"),
    # GitHub App token (ghs_)
    re.compile(r"ghs_[A-Za-z0-9]{36,}"),
    # GitHub OAuth token (gho_)
    re.compile(r"gho_[A-Za-z0-9]{36,}"),
    # GitLab PAT (glpat-)
    re.compile(r"glpat-[A-Za-z0-9\-_]{20,}"),
    # Bearer token in headers
    re.compile(r"(?i)Bearer\s+[A-Za-z0-9\-_.~+/]+=*"),
    # Private key headers
    re.compile(r"-----BEGIN\s+(?:RSA\s+|EC\s+|DSA\s+)?PRIVATE\s+KEY-----"),
    # Connection strings with passwords
    re.compile(
        r"(?i)(?:postgres|mysql|mongodb|redis)://[^:\s]+:[^@\s]+@",
    ),
    # Generic API key/secret patterns (key=value context)
    re.compile(
        r"(?i)(?:api_key|apikey|api_secret|secret_key|auth_token)"
        r"[\s=:\"']+[A-Za-z0-9\-_.]{16,}",
    ),
]

# PII patterns — email, IP (with loopback exemption), phone
_LOOPBACK_PREFIXES = ("127.", "0.", "::1")

_PII_EMAIL = re.compile(
    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
)
_PII_IP = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b",
)
_PII_PHONE = re.compile(
    r"(?:\+?\d{1,3}[-.\s]?)?"
    r"(?:\(?\d{2,4}\)?[-.\s]?)"
    r"\d{3,4}[-.\s]?\d{3,4}",
)

PII_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (_PII_EMAIL, "email"),
    (_PII_IP, "ip"),
    (_PII_PHONE, "phone"),
]


def redact_secrets(text: str) -> tuple[str, int]:
    """Scan text for credential patterns and redact them.

    Returns (redacted_text, count) where count is the total
    number of secrets found and replaced.
    """
    count = 0
    for pattern in SECRET_PATTERNS:
        matches = pattern.findall(text)
        if matches:
            count += len(matches)
            text = pattern.sub("[REDACTED]", text)
    return text, count


def contains_secrets(text: str) -> bool:
    """Quick check if text contains any secret patterns."""
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


def redact_pii(text: str) -> tuple[str, int]:
    """Scan text for PII patterns and redact them (S110).

    Exempts loopback IPs (127.x.x.x, 0.x.x.x, ::1).

    Returns (redacted_text, count).
    """
    count = 0

    # Handle emails
    email_matches = _PII_EMAIL.findall(text)
    if email_matches:
        count += len(email_matches)
        text = _PII_EMAIL.sub("[PII REDACTED]", text)

    # Handle IPs with loopback exemption
    def _replace_ip(match: re.Match[str]) -> str:
        nonlocal count
        ip = match.group()
        if any(ip.startswith(prefix) for prefix in _LOOPBACK_PREFIXES):
            return ip
        count += 1
        return "[PII REDACTED]"

    text = _PII_IP.sub(_replace_ip, text)

    # Handle phone numbers
    phone_matches = _PII_PHONE.findall(text)
    if phone_matches:
        count += len(phone_matches)
        text = _PII_PHONE.sub("[PII REDACTED]", text)

    return text, count
