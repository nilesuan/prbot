"""Secret and PII redaction (story-5-4, story-6-4).

Output-side safety net: scans final comment text for credential patterns and
PII before posting.

B6: the patterns used to fire on ordinary review prose and miss the content
they were written for. "Use a Bearer token" became "[REDACTED]", an
infrastructure finding about 10.0.0.0/8 lost the address it was about, and a
real phone number went through untouched while a hash did not. A control that
destroys the finding it is protecting is worse than no control, because the
reviewer cannot tell what was removed or why.

Two rules shape what is here:

- A pattern must require evidence that a value is a credential, not merely
  that a credential-sounding word appears nearby.
- Where a label makes the redaction legible ("api_key=[REDACTED]"), the label
  is kept. A bare [REDACTED] tells the reader nothing about what was found.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prbot.review.models import Finding

_REDACTED = "[REDACTED]"
_PII_REDACTED = "[PII REDACTED]"

# Patterns may declare two optional named groups:
#   keep  — a prefix preserved in the output, such as the label of a secret
#   tail  — a suffix preserved in the output, such as a delimiter
# Everything between them is replaced.
SECRET_PATTERNS: list[re.Pattern[str]] = [
    # AWS Access Key ID: AKIA is a long-lived user key, ASIA a temporary
    # STS key. ASIA is what OIDC federation hands a CI job.
    re.compile(r"(?:AKIA|ASIA)[0-9A-Z]{16}"),
    # AWS Secret Key — requires the label as context (GAP-8)
    re.compile(
        r"(?i)(?P<keep>(?:aws_secret_access_key|secret_access_key|aws_secret)"
        r"[\s=:\"']+)[A-Za-z0-9/+=]{40}",
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
    # Bearer token. The value must be long enough to be a credential: the
    # unqualified pattern matched the English words "Bearer token".
    re.compile(
        r"(?i)(?P<keep>Bearer\s+)[A-Za-z0-9\-_.~+/]{20,}={0,2}",
    ),
    # Private key headers
    re.compile(r"-----BEGIN\s+(?:RSA\s+|EC\s+|DSA\s+)?PRIVATE\s+KEY-----"),
    # Connection strings with passwords — the scheme and user are kept so the
    # reader can still see which connection string was flagged.
    re.compile(
        r"(?i)(?P<keep>(?:postgres|postgresql|mysql|mongodb|redis)://"
        r"[^:\s]+:)[^@\s]+(?P<tail>@)",
    ),
    # Generic API key/secret patterns (key=value context)
    re.compile(
        r"(?i)(?P<keep>(?:api_key|apikey|api_secret|secret_key|auth_token)"
        r"[\s=:\"']+)[A-Za-z0-9\-_.]{16,}",
    ),
]

_PII_EMAIL = re.compile(
    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
)

_PII_IP = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b",
)

# A phone number needs a country code or a bracketed area code. Without one,
# any run of grouped digits qualified: a hash, a byte offset and a date all
# matched, while a genuine international number did not.
_PII_PHONE = re.compile(
    r"(?:"
    r"\+\d{1,3}[\s.-]?(?:\(?\d{1,4}\)?[\s.-]?){1,3}\d{3,4}[\s.-]?\d{3,4}"
    r"|\(\d{2,4}\)\s?\d{3,4}[\s.-]?\d{3,4}"
    r")",
)

PII_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (_PII_EMAIL, "email"),
    (_PII_IP, "ip"),
    (_PII_PHONE, "phone"),
]


def _replace_secret(match: re.Match[str]) -> str:
    """Replace the credential, keeping any labelled context around it."""
    groups = match.groupdict()
    return f"{groups.get('keep') or ''}{_REDACTED}{groups.get('tail') or ''}"


def redact_secrets(text: str) -> tuple[str, int]:
    """Scan text for credential patterns and redact them.

    Returns (redacted_text, count) where count is the total number of
    secrets found and replaced.
    """
    count = 0
    for pattern in SECRET_PATTERNS:
        text, hits = pattern.subn(_replace_secret, text)
        count += hits
    return text, count


def contains_secrets(text: str) -> bool:
    """Quick check if text contains any secret patterns."""
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


def _is_public_address(value: str) -> bool:
    """Whether an address could identify a person or a real host.

    Private, loopback, link-local, reserved, multicast and documentation
    ranges appear in code and infrastructure constantly and identify nobody.
    Redacting them removed the substance of the finding: an infrastructure
    review that cannot name the CIDR it is complaining about is useless.
    """
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
        # 192.0.2.0/24, 198.51.100.0/24 and 203.0.113.0/24 are the ranges
        # documentation is required to use, so they are never real.
        or address in ipaddress.ip_network("192.0.2.0/24")
        or address in ipaddress.ip_network("198.51.100.0/24")
        or address in ipaddress.ip_network("203.0.113.0/24")
    )


def redact_pii(text: str) -> tuple[str, int]:
    """Scan text for PII patterns and redact them (S110).

    Returns (redacted_text, count).
    """
    count = 0

    email_matches = _PII_EMAIL.findall(text)
    if email_matches:
        count += len(email_matches)
        text = _PII_EMAIL.sub(_PII_REDACTED, text)

    def _replace_ip(match: re.Match[str]) -> str:
        nonlocal count
        address = match.group()
        if not _is_public_address(address):
            return address
        count += 1
        return _PII_REDACTED

    text = _PII_IP.sub(_replace_ip, text)

    text, phone_hits = _PII_PHONE.subn(_PII_REDACTED, text)
    count += phone_hits

    return text, count


def redact_finding_pii(finding: Finding) -> tuple[Finding, int]:
    """Redact PII from every prose field of a finding (B6).

    Only description was redacted before, so the same address or address
    left in the title or the suggestion went through untouched. file_path is
    deliberately left alone: a path is not personal data, and rewriting it
    breaks the link between the finding and the code.
    """
    total = 0
    cleaned: dict[str, str] = {}
    for field in ("title", "description", "suggestion"):
        value = getattr(finding, field)
        if not value:
            continue
        value, count = redact_pii(value)
        cleaned[field] = value
        total += count
    if not cleaned:
        return finding, 0
    return replace(finding, **cleaned), total
