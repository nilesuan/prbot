"""Shared HTTP retry for the VCS adapters (D3).

Neither adapter retried anything. A single 429 or 503 aborted the whole run,
Retry-After was ignored, and the two adapters carried near-identical request
code so a fix had to be made twice.

What is retried is deliberately narrow. A rate limit and a server error are
statements that the request may succeed later. A 401, 403, 404 or malformed
body are not, and retrying them wastes the time budget on a request that
cannot start working.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from prbot.exceptions import VCSError, VCSRateLimitError, VCSServerError

logger = logging.getLogger(__name__)

# Four attempts total. Beyond that the CI job's own timeout is the more
# useful limit, and a persistent 5xx is not going to clear in seconds.
MAX_ATTEMPTS = 4

# Exponential base. Jitter is applied on top so that several jobs throttled
# by the same API do not all retry on the same beat.
RETRY_BASE_SECONDS = 1.0

# Upper bound on an honoured Retry-After. A server asking for an hour is
# telling us to give up, not to hold a CI runner open.
MAX_RETRY_AFTER_SECONDS = 60.0

_RETRYABLE = (VCSRateLimitError, VCSServerError)


def parse_retry_after(value: str | None) -> float | None:
    """Parse a Retry-After header, in seconds or as an HTTP date."""
    if not value:
        return None
    raw = value.strip()
    try:
        return max(0.0, float(int(raw)))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    import datetime

    now = datetime.datetime.now(when.tzinfo or datetime.UTC)
    return max(0.0, (when - now).total_seconds())


def backoff_seconds(attempt: int, retry_after: str | None = None) -> float:
    """Delay before the next attempt, honouring Retry-After when given."""
    advised = parse_retry_after(retry_after)
    if advised is not None:
        return min(advised, MAX_RETRY_AFTER_SECONDS)
    base = RETRY_BASE_SECONDS * (2**attempt)
    return min(base + random.uniform(0.0, base / 2), MAX_RETRY_AFTER_SECONDS)


async def send_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    classify: Callable[[httpx.Response, str, str], None],
    label: str,
    **kwargs: Any,
) -> httpx.Response:
    """Send a request, retrying rate limits, server errors and transport faults.

    `classify` raises the typed VCSError for a non-2xx response; anything it
    raises that is not retryable propagates on the first attempt.
    """
    last_error: Exception | None = None

    for attempt in range(MAX_ATTEMPTS):
        try:
            response = await client.request(method, url, **kwargs)
        except httpx.TimeoutException as e:
            last_error = VCSError(f"{label} API timed out: {method} {url}")
            last_error.__cause__ = e
        except httpx.ConnectError as e:
            last_error = VCSError(f"{label} API unreachable: {method} {url}")
            last_error.__cause__ = e
        else:
            try:
                classify(response, method, url)
            except _RETRYABLE as e:
                last_error = e
                if attempt == MAX_ATTEMPTS - 1:
                    raise
                delay = backoff_seconds(
                    attempt, response.headers.get("retry-after"),
                )
                logger.warning(
                    "%s API %s on %s %s, retrying in %.1fs (attempt %d/%d)",
                    label, type(e).__name__, method, url, delay,
                    attempt + 1, MAX_ATTEMPTS,
                )
                await asyncio.sleep(delay)
                continue
            return response

        if attempt == MAX_ATTEMPTS - 1:
            raise last_error
        delay = backoff_seconds(attempt)
        logger.warning(
            "%s API transport error on %s %s, retrying in %.1fs "
            "(attempt %d/%d)",
            label, method, url, delay, attempt + 1, MAX_ATTEMPTS,
        )
        await asyncio.sleep(delay)

    # Unreachable: the loop either returns or raises on its last attempt.
    raise last_error or VCSError(f"{label} API request failed: {method} {url}")
