"""AWS credential validation (story-2-3).

Checks whether AWS credentials are session-scoped (OIDC/STS)
or long-lived (IAM user keys), and logs appropriate warnings.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def validate_aws_session_credentials() -> bool:
    """Check if AWS credentials are session-scoped (OIDC/STS).

    Returns True if AWS_SESSION_TOKEN is present (session credentials).
    Returns False and logs a warning if only long-lived credentials are found.
    Returns False silently if no AWS credentials are configured at all.
    """
    has_access_key = bool(os.environ.get("AWS_ACCESS_KEY_ID"))
    has_session_token = bool(os.environ.get("AWS_SESSION_TOKEN"))

    if has_session_token:
        return True

    if has_access_key:
        logger.warning(
            "AWS credentials appear to be long-lived (no AWS_SESSION_TOKEN). "
            "Consider using OIDC federation or STS AssumeRole for short-lived "
            "credentials in CI environments."
        )
        return False

    return False
