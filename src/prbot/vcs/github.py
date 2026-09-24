"""GitHub REST API adapter (stories 3-3, 3-4).

Implements VCSAdapter protocol for GitHub.com and GitHub Enterprise.
Uses httpx async client with Bearer token authentication.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx

from prbot.auth.token import TokenResult
from prbot.exceptions import (
    VCSAuthError,
    VCSError,
    VCSNotFoundError,
    VCSRateLimitError,
    VCSResponseError,
    VCSServerError,
)
from prbot.vcs.models import (
    FileDiff,
    InlineComment,
    PRDiff,
    PRMetadata,
    ReviewThread,
    validate_response,
)
from prbot.vcs.retry import send_with_retry

logger = logging.getLogger(__name__)

# State marker for bot comments
_STATE_MARKER = "<!-- prbot:state:"

# GitHub API file limit before truncation
_GITHUB_FILE_LIMIT = 3000

# SEC-INPUT-04: an upper bound on a file fetched for context. The sizes are
# chosen by the contributor whose branch is under review, every non-removed
# file in the diff is fetched, and build_context_excerpt only ever uses a
# window around the hunks, so anything past this is retained for nothing.
# 2 MiB is far above any file a human reads in review and far below what
# would hurt a runner.
MAX_CONTEXT_BYTES = 2 * 1024 * 1024


# Hard cap on pages followed (D3). At per_page=100 this is 10,000 items,
# past any pull request worth reviewing. Without it a server returning a
# Link header that points back at itself holds the CI job open until the
# job timeout, with no log line saying why.
_MAX_PAGES = 100

# Review thread resolution state lives only in GraphQL; REST does not expose
# it. The query is kept here rather than inline so the shape is readable.
_THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          isResolved
          resolvedBy { login }
          path
          line
          comments(first: 1) { nodes { databaseId body author { login } } }
        }
      }
    }
  }
}
"""

_RESOLVE_MUTATION = """
mutation($threadId: ID!) {
  resolveReviewThread(input: {threadId: $threadId}) { thread { id } }
}
"""

_UNRESOLVE_MUTATION = """
mutation($threadId: ID!) {
  unresolveReviewThread(input: {threadId: $threadId}) { thread { id } }
}
"""


class GitHubAdapter:
    """GitHub REST API adapter implementing VCSAdapter protocol."""

    def __init__(
        self,
        token: TokenResult,
        repo: str,
        pr_number: int,
        base_url: str = "https://api.github.com",
    ) -> None:
        self._repo = repo
        self._pr_number = pr_number
        self._base_url = base_url.rstrip("/")
        self._authenticated_user: str | None = None
        # GEN-ARCH-03: get_pr_metadata and get_diff both need the PR
        # payload and both used to fetch it, so every run paid for two
        # identical GETs to the same endpoint.
        self._pr_payload: dict[str, Any] | None = None
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": token.as_bearer_header(),
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30.0,
        )

    async def _fetch_pr(self) -> dict[str, Any]:
        """The PR payload, fetched once and cached for the run."""
        if self._pr_payload is None:
            self._pr_payload = await self._request(
                "GET",
                f"{self._base_url}/repos/{self._repo}"
                f"/pulls/{self._pr_number}",
            )
        return self._pr_payload

    async def get_pr_metadata(self) -> PRMetadata:
        """Fetch PR metadata with state normalization and null coercion."""
        data = await self._fetch_pr()
        validate_response(data, [
            "head.sha", "base.sha", "head.ref", "base.ref",
            "state", "user.login", "number",
        ])

        # State normalization: GitHub uses "closed" for both closed and merged
        state = data["state"]
        if state == "closed" and data.get("merged"):
            state = "merged"

        # Fork detection
        head_repo = data.get("head", {}).get("repo") or {}
        base_repo = data.get("base", {}).get("repo") or {}
        is_fork = head_repo.get("full_name") != base_repo.get("full_name")

        return PRMetadata(
            title=data.get("title") or "",
            body=data.get("body") or "",
            state=state,
            head_sha=data["head"]["sha"],
            base_sha=data["base"]["sha"],
            head_ref=data["head"]["ref"],
            base_ref=data["base"]["ref"],
            author=data["user"]["login"],
            number=data["number"],
            is_draft=bool(data.get("draft")),
            is_fork=is_fork,
        )

    async def get_diff(self) -> PRDiff:
        """Fetch PR diff via paginated List PR Files endpoint."""
        url = (
            f"{self._base_url}/repos/{self._repo}"
            f"/pulls/{self._pr_number}/files"
        )
        all_files: list[dict[str, Any]] = []
        await self._paginate(url, all_files.extend)

        files = [
            FileDiff(
                path=f.get("filename", ""),
                status=_normalize_file_status(f.get("status", "modified")),
                patch=f.get("patch", ""),
                additions=f.get("additions", 0),
                deletions=f.get("deletions", 0),
                previous_path=f.get("previous_filename"),
            )
            for f in all_files
        ]

        # Get SHAs from the PR payload, fetched once per run
        pr_data = await self._fetch_pr()
        head_sha = pr_data.get("head", {}).get("sha", "")
        base_sha = pr_data.get("base", {}).get("sha", "")

        return PRDiff(
            files=files,
            head_sha=head_sha,
            base_sha=base_sha,
            truncated=len(files) >= _GITHUB_FILE_LIMIT,
        )

    async def get_file_content(self, path: str, ref: str) -> str | None:
        """Fetch a file's text at a revision (B8)."""
        url = (
            f"{self._base_url}/repos/{self._repo}"
            f"/contents/{quote(path)}"
        )
        try:
            response = await send_with_retry(
                self._client, "GET", url,
                classify=_classify_response, label="GitHub",
                params={"ref": ref},
                headers={
                    "Accept": "application/vnd.github.raw+json",
                    # SEC-INPUT-04: ask for at most the bound.
                    # send_with_retry uses the non-streaming
                    # API, so without this httpx buffers the
                    # whole body before the length is checked
                    # and the bound limits what is retained
                    # rather than what is allocated.
                    "Range": f"bytes=0-{MAX_CONTEXT_BYTES - 1}",
                },
            )
        except VCSError as e:
            logger.info("context.unavailable path=%s: %s", path, e)
            return None

        # 206 means the server honoured the Range and had more to give, so
        # the file is over the bound. 200 with an oversized body means it
        # ignored the Range; the length check still catches that.
        if response.status_code == 206 or (
            len(response.content) > MAX_CONTEXT_BYTES
        ):
            logger.info(
                "context.too_large path=%s bytes=%d limit=%d status=%d",
                path, len(response.content), MAX_CONTEXT_BYTES,
                response.status_code,
            )
            return None
        return response.text

    async def get_authenticated_user(self) -> str:
        """Get authenticated user login, cached after first call.

        Returns "" when the identity cannot be established. GET /user is not
        available to a GitHub App installation token, which is exactly what
        secrets.GITHUB_TOKEN is, and GitHub answers 403 rather than 200.
        Raising there would fail every GitHub Actions run before a review
        starts.

        SEC-AUTH-02: callers read an empty login as "identity unknown" and
        match threads on prbot's marker alone. That is weaker, because anyone
        who can comment can paste a marker, but it is the degradation
        reconcile() already documents, and the alternative is no review at
        all. A 401 still raises: that means the credential is bad, not that
        the endpoint is the wrong one. A throttled 403 still raises too,
        since _request classifies it as VCSRateLimitError.
        """
        if self._authenticated_user is None:
            try:
                data = await self._request("GET", f"{self._base_url}/user")
            except VCSAuthError as e:
                if e.status_code != 403:
                    raise
                logger.warning(
                    "Could not read the authenticated user: GET /user is not "
                    "available to an installation token. Thread ownership "
                    "will be matched on the prbot marker alone.",
                )
                self._authenticated_user = ""
            else:
                self._authenticated_user = data.get("login", "")
        return self._authenticated_user

    async def find_bot_comment(self) -> tuple[int, str] | None:
        """Find existing bot comment with early-exit pagination."""
        bot_user = await self.get_authenticated_user()
        url = (
            f"{self._base_url}/repos/{self._repo}"
            f"/issues/{self._pr_number}/comments"
        )

        result: tuple[int, str] | None = None

        def _check_comments(items: list[dict[str, Any]]) -> bool:
            """Process comment page. Returns True to stop pagination."""
            nonlocal result
            for comment in items:
                author = comment.get("user", {}).get("login", "")
                body = comment.get("body", "")
                if author == bot_user and _STATE_MARKER in body:
                    result = (comment["id"], body)
                    return True  # Early exit
            return False

        await self._paginate(url, _check_comments)
        return result

    async def post_comment(self, body: str) -> int:
        """Post a new comment on the PR."""
        url = (
            f"{self._base_url}/repos/{self._repo}"
            f"/issues/{self._pr_number}/comments"
        )
        data = await self._request("POST", url, json={"body": body})
        return data["id"]

    async def update_comment(self, comment_id: int, body: str) -> None:
        """Update an existing comment by ID."""
        url = (
            f"{self._base_url}/repos/{self._repo}"
            f"/issues/comments/{comment_id}"
        )
        await self._request("PATCH", url, json={"body": body})

    async def post_or_update_comment(self, body: str) -> int:
        """Idempotent: find existing bot comment → update, or create new."""
        existing = await self.find_bot_comment()
        if existing:
            comment_id, _ = existing
            await self.update_comment(comment_id, body)
            return comment_id
        return await self.post_comment(body)

    async def submit_review(
        self,
        body: str,
        event: str,
        comments: list[InlineComment],
        *,
        head_sha: str,
        base_sha: str,
    ) -> int:
        """Submit a pull request review with inline comments (C1, C2).

        commit_id pins the review to the revision it was produced from, so a
        push that lands mid-review does not silently move the comments onto
        code nobody looked at.

        Two things GitHub refuses are both survivable. A position it rejects
        is usually a line that has moved. An event it rejects is usually the
        token: GitHub does not permit the Actions token to approve a pull
        request, and nobody may approve their own. Each fallback gives up one
        of those and keeps the rest, because the verdict still reaches the
        exit code and losing the review entirely does not.
        """
        url = (
            f"{self._base_url}/repos/{self._repo}"
            f"/pulls/{self._pr_number}/reviews"
        )
        rendered = [_github_comment(comment) for comment in comments]

        attempts: list[tuple[str, list[dict[str, Any]]]] = [(event, rendered)]
        if rendered:
            attempts.append((event, []))
        if event != "COMMENT":
            attempts.append(("COMMENT", rendered))
            if rendered:
                attempts.append(("COMMENT", []))

        error: VCSResponseError | None = None
        for attempt_event, attempt_comments in attempts:
            if error is not None:
                logger.warning(
                    "GitHub rejected the review (%s); retrying as %s with "
                    "%d inline comment(s)",
                    error, attempt_event, len(attempt_comments),
                )
            try:
                data = await self._request("POST", url, json={
                    "body": body,
                    "event": attempt_event,
                    "commit_id": head_sha,
                    "comments": attempt_comments,
                })
            except VCSResponseError as e:
                error = e
                continue
            return data["id"]

        assert error is not None
        raise error

    def _graphql_url(self) -> str:
        """GraphQL lives beside the REST root, not under it.

        github.com serves it at api.github.com/graphql; Enterprise serves
        REST at /api/v3 and GraphQL at /api/graphql.
        """
        if self._base_url.endswith("/api/v3"):
            return self._base_url[: -len("/api/v3")] + "/api/graphql"
        return f"{self._base_url}/graphql"

    async def _graphql(
        self, query: str, variables: dict[str, Any],
    ) -> dict[str, Any]:
        response = await send_with_retry(
            self._client, "POST", self._graphql_url(),
            classify=_classify_response, label="GitHub",
            json={"query": query, "variables": variables},
        )
        payload = _decode_json(response, "POST", self._graphql_url())
        if payload.get("errors"):
            raise VCSResponseError(
                f"GitHub GraphQL returned errors: {payload['errors']}"
            )
        return payload.get("data") or {}

    async def list_review_threads(self) -> list[ReviewThread]:
        """List review threads, with resolution state where available (C8)."""
        owner, _, name = self._repo.partition("/")
        threads: list[ReviewThread] = []
        cursor: str | None = None

        try:
            for _ in range(_MAX_PAGES):
                data = await self._graphql(
                    _THREADS_QUERY,
                    {
                        "owner": owner,
                        "name": name,
                        "number": self._pr_number,
                        "after": cursor,
                    },
                )
                node = (
                    data.get("repository", {})
                    .get("pullRequest", {})
                    .get("reviewThreads", {})
                )
                for item in node.get("nodes") or []:
                    comments = (item.get("comments") or {}).get("nodes") or []
                    if not comments:
                        continue
                    author = (comments[0].get("author") or {}).get(
                        "login", "",
                    )
                    threads.append(ReviewThread(
                        id=item["id"],
                        comment_id=comments[0].get("databaseId", 0),
                        body=comments[0].get("body", ""),
                        resolved=bool(item.get("isResolved")),
                        path=item.get("path"),
                        line=item.get("line"),
                        author=author,
                        resolved_by=(item.get("resolvedBy") or {}).get(
                            "login", "",
                        ),
                    ))
                page = node.get("pageInfo") or {}
                if not page.get("hasNextPage"):
                    break
                cursor = page.get("endCursor")
            return threads
        except VCSError as e:
            # A fine-grained token without GraphQL access, or an Enterprise
            # install with it disabled. Falling back to REST loses resolution
            # state, which makes prbot re-report a thread a human closed; that
            # is worse than ideal and much better than no review.
            logger.warning(
                "GitHub GraphQL unavailable (%s); falling back to REST "
                "review comments without resolution state",
                e,
            )
            return await self._rest_review_threads()

    async def _rest_review_threads(self) -> list[ReviewThread]:
        """Top-level review comments, resolution state unknown."""
        url = (
            f"{self._base_url}/repos/{self._repo}"
            f"/pulls/{self._pr_number}/comments"
        )
        collected: list[dict[str, Any]] = []
        await self._paginate(url, collected.extend)
        return [
            ReviewThread(
                id=str(c.get("id", "")),
                comment_id=c.get("id", 0),
                body=c.get("body", ""),
                resolved=False,
                path=c.get("path"),
                line=c.get("line"),
                author=(c.get("user") or {}).get("login", ""),
            )
            for c in collected
            if c.get("in_reply_to_id") is None
        ]

    async def reply_to_thread(
        self, thread: ReviewThread, body: str,
    ) -> None:
        """Reply into an existing review thread."""
        url = (
            f"{self._base_url}/repos/{self._repo}"
            f"/pulls/{self._pr_number}/comments/{thread.comment_id}/replies"
        )
        await self._request("POST", url, json={"body": body})

    async def resolve_thread(self, thread: ReviewThread) -> bool:
        """Resolve a review thread via GraphQL."""
        if not thread.id.startswith("T_") and not thread.id.startswith("PRRT"):
            # A REST fallback id is not a GraphQL node id.
            logger.info("Cannot resolve thread %s: no node id", thread.id)
            return False
        try:
            await self._graphql(_RESOLVE_MUTATION, {"threadId": thread.id})
        except VCSError as e:
            logger.warning("Could not resolve thread %s: %s", thread.id, e)
            return False
        return True

    async def unresolve_thread(self, thread: ReviewThread) -> bool:
        """Reopen a review thread via GraphQL."""
        if not thread.id.startswith("T_") and not thread.id.startswith("PRRT"):
            logger.info("Cannot reopen thread %s: no node id", thread.id)
            return False
        try:
            await self._graphql(_UNRESOLVE_MUTATION, {"threadId": thread.id})
        except VCSError as e:
            logger.warning("Could not reopen thread %s: %s", thread.id, e)
            return False
        return True

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Make an HTTP request with retry and error classification (G4-15)."""
        response = await send_with_retry(
            self._client, method, url,
            classify=_classify_response, label="GitHub", **kwargs,
        )
        return _decode_json(response, method, url)

    async def _paginate(
        self,
        url: str,
        callback: Callable[[list[dict[str, Any]]], bool | None],
    ) -> None:
        """Paginate GitHub API using Link: rel="next" header.

        Args:
            url: Initial page URL.
            callback: Called with each page's items. Return True for early exit.
        """
        params: dict[str, Any] = {"per_page": 100}
        current_url: str | None = url
        # The full origin, not just the host (SEC-CRED-02). Comparing the
        # host alone let 'http://api.github.com/...' through, which sends the
        # Authorization header in cleartext, and let a different port on the
        # same host through to a different listener.
        _initial = httpx.URL(url)
        origin = (_initial.scheme, _initial.host, _initial.port)
        pages = 0

        while current_url:
            pages += 1
            if pages > _MAX_PAGES:
                raise VCSError(
                    f"GitHub API pagination exceeded {_MAX_PAGES} pages "
                    f"for {url}; refusing to follow further"
                )

            response = await send_with_retry(
                self._client, "GET", current_url,
                classify=_classify_response, label="GitHub", params=params,
            )
            items = _expect_list(
                _decode_json(response, "GET", current_url),
                "GET",
                current_url,
            )

            if callback(items):
                return  # Early exit requested

            # Clear params after first request (Link URL includes them)
            params = {}

            # Follow Link: rel="next"
            current_url = _parse_next_link(
                response.headers.get("link", ""),
            )

            # D3: the next URL comes from the server and is followed with the
            # Authorization header attached. A compromised or misconfigured
            # host must not be able to redirect the token somewhere else.
            if current_url:
                nxt = httpx.URL(current_url)
                if (nxt.scheme, nxt.host, nxt.port) != origin:
                    raise VCSError(
                        f"GitHub API pagination pointed at a different "
                        f"origin: {nxt.scheme}://{nxt.host}:{nxt.port} is "
                        f"not {origin[0]}://{origin[1]}:{origin[2]}"
                    )


def _decode_json(
    response: httpx.Response, method: str, url: str,
) -> Any:
    """Decode a response body, classifying a non-JSON body (D4).

    A json.JSONDecodeError is not a PrBotError, so letting it escape exits
    the process with code 1 and reports an upstream HTML error page to CI
    as a blocking review.
    """
    try:
        return response.json()
    except ValueError as e:
        raise VCSResponseError(
            f"GitHub API returned a body that is not valid JSON: "
            f"{method} {url}. {response.text[:200]}"
        ) from e


def _expect_list(
    payload: Any, method: str, url: str,
) -> list[dict[str, Any]]:
    """Require a JSON array where the API contract promises one (D4)."""
    if not isinstance(payload, list):
        raise VCSResponseError(
            f"GitHub API returned {type(payload).__name__} where a list "
            f"was expected: {method} {url}"
        )
    return payload


def _classify_response(
    response: httpx.Response, method: str, url: str,
) -> None:
    """Classify HTTP response status into typed exceptions (G4-15)."""
    status = response.status_code
    if 200 <= status < 300:
        return

    body_text = response.text[:200]

    if status == 401:
        raise VCSAuthError(
            f"GitHub API authentication failed (401): {method} {url}",
            status_code=401,
        )
    if status == 403:
        # D3: GitHub signals its primary rate limit with 403 and an exhausted
        # quota header, and a secondary limit with 403 plus Retry-After.
        # Classifying either as an auth failure reported a transient
        # throttle to CI as a permanent configuration error, and skipped the
        # retry that would have cleared it.
        remaining = response.headers.get("x-ratelimit-remaining")
        if remaining == "0" or response.headers.get("retry-after"):
            raise VCSRateLimitError(
                f"GitHub API rate_limited (403): {method} {url}. {body_text}"
            )
        raise VCSAuthError(
            f"GitHub API forbidden (403): {method} {url}. {body_text}",
            status_code=403,
        )
    if status == 404:
        raise VCSNotFoundError(
            f"GitHub API not found (404): {method} {url}"
        )
    if status == 429:
        raise VCSRateLimitError(
            f"GitHub API rate_limited (429): {method} {url}"
        )
    if status >= 500:
        raise VCSServerError(
            f"GitHub API server error ({status}): {method} {url}"
        )
    raise VCSResponseError(
        f"GitHub API unexpected status ({status}): {method} {url}. "
        f"{body_text}"
    )


def _github_comment(comment: InlineComment) -> dict[str, Any]:
    """Render an inline comment in the review-comments shape."""
    payload: dict[str, Any] = {
        "path": comment.path,
        "line": comment.line,
        "side": "RIGHT",
        "body": comment.body,
    }
    if comment.start_line is not None and comment.start_line < comment.line:
        payload["start_line"] = comment.start_line
        payload["start_side"] = "RIGHT"
    return payload


def _parse_next_link(link_header: str) -> str | None:
    """Extract next page URL from GitHub Link header."""
    for part in link_header.split(","):
        if 'rel="next"' in part:
            # Extract URL between < and >
            start = part.find("<")
            end = part.find(">")
            if start != -1 and end != -1:
                return part[start + 1 : end]
    return None


def _normalize_file_status(status: str) -> str:
    """Normalize GitHub file status to standard values."""
    mapping = {
        "added": "added",
        "removed": "removed",
        "modified": "modified",
        "renamed": "renamed",
        "copied": "added",
        "changed": "modified",
    }
    return mapping.get(status, "modified")
