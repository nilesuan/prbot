"""Configuration model with validators (story-1-2).

Merge priority: CLI args > env vars (PRBOT_ prefix) > TOML config > defaults.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import socket
import tomllib
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator

from prbot.exceptions import ConfigError

logger = logging.getLogger(__name__)

# --- SSRF validation (reused by auth and VCS) ---

_METADATA_HOSTNAMES = frozenset({
    "169.254.169.254",
    "metadata.google.internal",
    "metadata.azure.internal",
    "fd00:ec2::254",
})


def _validate_url_not_internal(url: str) -> str:
    """Reject URLs pointing to private/loopback/metadata IPs (G-16).

    Raises ConfigError if the URL targets an internal address.
    Allows http only if PRBOT_ALLOW_HTTP is set.

    Not absolute (SEC-DATA-02). This resolves the name, and httpx resolves
    it again when it opens the socket, so a record with a short TTL that
    answers publicly here and privately there is not caught. Callers narrow
    that window by validating immediately before building a client, and
    GitHub pagination is additionally pinned to the validated origin, but
    closing it entirely would mean pinning the connection to the resolved
    address. Reaching it requires already controlling the base URL, so it is
    a defence-in-depth gap rather than an independently reachable hole.
    """
    parsed = urlparse(url)

    if not parsed.scheme:
        raise ConfigError(f"URL must include scheme: {url}")

    if parsed.scheme == "http" and not os.environ.get("PRBOT_ALLOW_HTTP"):
        raise ConfigError(
            f"HTTP URLs are not allowed (use HTTPS): {url}. "
            "Set PRBOT_ALLOW_HTTP=1 to override."
        )

    if parsed.scheme not in ("http", "https"):
        raise ConfigError(f"URL scheme must be http or https: {url}")

    hostname = parsed.hostname or ""

    if hostname in _METADATA_HOSTNAMES:
        raise ConfigError(f"SSRF: URL points to cloud metadata endpoint: {url}")

    if not hostname:
        raise ConfigError(f"URL has no host: {url}")

    # Resolve before trusting (SEC-DATA-02). The previous check tried
    # ipaddress.ip_address on the hostname and allowed anything that raised
    # ValueError, so a DNS name pointing at the metadata endpoint passed
    # untouched, and so did non-dotted-quad literal forms such as the decimal
    # 2852039166. Every address the name resolves to has to be acceptable,
    # not just the literal spelling of it.
    for addr in _resolve_addresses(hostname, url):
        reason = _internal_reason(addr)
        if reason:
            raise ConfigError(
                f"SSRF: {url} resolves to the {reason} address {addr}"
            )

    return url


def _internal_reason(
    addr: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> str:
    """Why an address is not reachable-by-us, or an empty string."""
    if addr.is_loopback:
        return "loopback"
    if addr.is_link_local:
        return "link-local"
    if addr.is_private:
        return "private"
    if addr.is_multicast:
        return "multicast"
    if addr.is_unspecified:
        return "unspecified"
    if addr.is_reserved:
        return "reserved"
    return ""


def _resolve_addresses(
    hostname: str, url: str,
) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Every address a hostname resolves to, literals included.

    ipaddress.ip_address recognises dotted-quad IPv4 and colon-form IPv6
    only. A decimal or hex host string such as "2852039166" raises
    ValueError here and falls through to getaddrinfo, which does accept it
    and is what actually catches that form (GEN-API-04).
    """
    try:
        return [ipaddress.ip_address(hostname)]
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as e:
        raise ConfigError(
            f"Could not resolve host for {url}: {e}"
        ) from e

    out: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        sockaddr = info[4]
        try:
            out.append(ipaddress.ip_address(sockaddr[0]))
        except ValueError:
            continue
    if not out:
        raise ConfigError(f"Could not resolve host for {url}")
    return out


# --- Config model ---

_REPO_PATTERN = re.compile(r"^[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+$")
# Two-letter area, one or more hyphenated parts, then a number. The
# single-part form rejected every partition with a qualifier in the
# name: us-gov-west-1, us-iso-east-1 and us-isob-east-1 (D5).
_AWS_REGION_PATTERN = re.compile(r"^[a-z]{2}(?:-[a-z]+)+-\d+$")
_SECRET_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9/_+=.@-]+$")
_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


_AGENT_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_CHECK_PREFIX_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*-$")


class AgentSpec(BaseModel, frozen=True):
    """One review agent (C5).

    The name selects the check spec, `{name}.md`, so it is validated as a
    single safe path segment rather than against an allowlist: an allowlist
    is what made adding an agent a code change in three modules.
    """

    name: str
    check_prefix: str
    model_id: str | None = None
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not _AGENT_NAME_PATTERN.match(v):
            raise ValueError(
                f"agent name must be lowercase letters, digits, hyphens or "
                f"underscores, starting with a letter, at most 32 "
                f"characters: {v!r}"
            )
        return v

    @field_validator("check_prefix")
    @classmethod
    def validate_check_prefix(cls, v: str) -> str:
        if not _CHECK_PREFIX_PATTERN.match(v):
            raise ValueError(
                f"check_prefix must be uppercase alphanumerics followed by a "
                f"hyphen, such as 'Q-' or 'IAC-': {v!r}"
            )
        return v


class SuppressionRule(BaseModel, frozen=True):
    """A finding this repository has decided not to be told about (C4).

    A reason is required. A suppression without one is indistinguishable from
    a bug six months later, and the count of suppressed findings is reported
    in every comment so that the list cannot quietly grow into a gag.
    """

    check_id: str
    reason: str
    path: str | None = None
    max_severity: Literal["critical", "high", "medium", "low", "info"] | None = (
        None
    )

    @field_validator("check_id")
    @classmethod
    def validate_check_id(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("check_id must not be empty")
        return v.strip()

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, v: str) -> str:
        if not v.strip():
            raise ValueError(
                "reason must not be empty: an undocumented suppression is "
                "indistinguishable from a bug later"
            )
        return v.strip()


# The roster prbot ships with. A repository overrides it wholesale by
# setting `agents`, which is also how a third agent is added.
DEFAULT_AGENTS: list[dict[str, str]] = [
    {"name": "general", "check_prefix": "Q-"},
    {"name": "security", "check_prefix": "S-"},
]


class PrBotConfig(BaseModel, frozen=True):
    """Immutable configuration for prbot."""

    platform: Literal["github", "gitlab"]
    repo: str
    pr_number: int = Field(gt=0)
    aws_region: str = "ap-southeast-2"
    allowed_regions: list[str] = Field(default_factory=lambda: ["ap-southeast-2"])
    confidence_threshold: int = Field(default=70, ge=0, le=100)
    # Minimum *confidence* at which a critical or high finding blocks.
    blocker_threshold: int = Field(default=70, ge=0, le=100)
    # Minimum *quality score* (0-100) a review must reach to pass. A
    # different quantity from blocker_threshold that happens to share a
    # range; comparing one against the other was a category error (B3).
    min_passing_score: int = Field(default=70, ge=0, le=100)
    general_model_id: str = "au.anthropic.claude-sonnet-5"
    security_model_id: str = "au.anthropic.claude-sonnet-5"
    max_diff_tokens: int = Field(default=100_000, gt=0)
    # B5: explicit cap on each agent response. Left to the Bedrock
    # default, output length is neither reproducible nor visible to the
    # cost estimate.
    max_output_tokens: int = Field(default=8192, gt=0)
    # B8: lines of surrounding head-revision code to include around
    # each hunk. 0 keeps the diff-only behaviour. Whether the extra
    # tokens buy precision is a question for measurement.
    context_lines: int = Field(default=0, ge=0, le=200)
    budget_limit_usd: float = Field(default=5.00, gt=0)
    timeout_seconds: int = Field(default=300, gt=0)
    api_base_url: str | None = None
    secret_name: str | None = None
    draft_behavior: Literal["skip", "review"] = "skip"
    # C1/C2: "review" submits a platform review, so each finding lands as a
    # comment on the line it is about and the verdict reaches the pull
    # request rather than only the exit code. That is the default, because a
    # review nobody reads is a review that did not happen and one summary
    # blob at the bottom of the page is what nobody reads. "comment" keeps
    # the older behaviour of a single summary comment, for a token that
    # cannot submit reviews or a team that does not want line comments.
    review_mode: Literal["comment", "review"] = "review"
    # B2: whether the patch content is datamarked. Metadata is always
    # datamarked, since the title, body and author are author-written
    # prose and the obvious injection vector. Whether marking the patch
    # as well helps or hurts finding precision is a question for
    # measurement, which this switch makes possible; see
    # scripts/measure_datamarking.py.
    datamark_diff: bool = True
    excluded_patterns: list[str] = Field(default_factory=list)
    # C5: the review roster. None means the shipped default of a general
    # and a security agent, whose models come from general_model_id and
    # security_model_id so existing configuration keeps working.
    agents: list[AgentSpec] | None = Field(default=None, min_length=1)
    # C4: findings this repository has decided not to be told about.
    suppress: list[SuppressionRule] = Field(default_factory=list)
    log_level: str = "INFO"
    # C7: optional metrics sinks. Without them the audit record is one
    # log line per run, which cannot answer how often reviews block or
    # what they cost over time.
    metrics_namespace: str | None = None
    metrics_file: str | None = None
    dry_run: bool = False
    # C3: review again even when this commit has already been reviewed.
    force_review: bool = False

    @field_validator("repo")
    @classmethod
    def validate_repo(cls, v: str) -> str:
        if not _REPO_PATTERN.match(v):
            raise ValueError(
                f"repo must be 'owner/name' with alphanumeric, dots, hyphens, "
                f"underscores only: {v!r}"
            )
        return v

    @field_validator("aws_region")
    @classmethod
    def validate_aws_region(cls, v: str) -> str:
        if not _AWS_REGION_PATTERN.match(v):
            raise ValueError(
                f"aws_region must match format like 'us-east-1': {v!r}"
            )
        return v

    @field_validator("api_base_url")
    @classmethod
    def validate_api_base_url(cls, v: str | None) -> str | None:
        if v is not None:
            _validate_url_not_internal(v)
        return v

    @field_validator("secret_name")
    @classmethod
    def validate_secret_name(cls, v: str | None) -> str | None:
        if v is not None:
            if ".." in v:
                raise ValueError(f"secret_name must not contain '..': {v!r}")
            if v.startswith("/"):
                raise ValueError(f"secret_name must not start with '/': {v!r}")
            if not _SECRET_NAME_PATTERN.match(v):
                raise ValueError(
                    f"secret_name contains invalid characters: {v!r}"
                )
        return v

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        upper = v.upper()
        if upper not in _LOG_LEVELS:
            raise ValueError(
                f"log_level must be one of {sorted(_LOG_LEVELS)}: {v!r}"
            )
        return upper

    @field_validator("agents")
    @classmethod
    def validate_unique_agent_names(
        cls, v: list[AgentSpec] | None,
    ) -> list[AgentSpec] | None:
        if v is not None:
            names = [a.name for a in v]
            duplicates = {n for n in names if names.count(n) > 1}
            if duplicates:
                raise ValueError(
                    f"duplicate agent names: {sorted(duplicates)}"
                )
        return v

    def agent_roster(self) -> list[AgentSpec]:
        """The agents this review will run, with models resolved.

        Raises:
            ConfigError: if every configured agent is disabled.
        """
        default_model = {
            "general": self.general_model_id,
            "security": self.security_model_id,
        }
        specs = self.agents
        if specs is None:
            specs = [AgentSpec(**spec) for spec in DEFAULT_AGENTS]

        resolved = [
            spec.model_copy(
                update={
                    "model_id": spec.model_id
                    or default_model.get(spec.name, self.general_model_id),
                },
            )
            for spec in specs
            if spec.enabled
        ]
        if not resolved:
            raise ConfigError(
                "at least one agent must be enabled; the whole roster is "
                "disabled"
            )
        return resolved

    @model_validator(mode="after")
    def validate_threshold_ordering(self) -> PrBotConfig:
        if self.blocker_threshold < self.confidence_threshold:
            raise ValueError(
                f"blocker_threshold ({self.blocker_threshold}) must be >= "
                f"confidence_threshold ({self.confidence_threshold})"
            )
        return self


def _in_ci(env_vars: dict[str, str]) -> bool:
    """Whether this process is running inside a CI job.

    Used to decide whether the working directory can be trusted to hold the
    operator's configuration rather than the reviewed branch's.
    """
    return bool(
        env_vars.get("GITHUB_ACTIONS") == "true"
        or env_vars.get("GITLAB_CI") == "true"
        or env_vars.get("CI") == "true",
    )


# --- Platform detection ---


def detect_platform() -> str | None:
    """Detect CI platform from environment variables.

    Returns 'github', 'gitlab', or None if not in CI.
    """
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return "github"
    if os.environ.get("GITLAB_CI") == "true":
        return "gitlab"
    return None


def detect_pr_context() -> dict[str, str]:
    """Extract repo and PR number from CI environment variables.

    Returns dict with 'repo' and 'pr_number' keys.
    Raises ConfigError if not in a PR/MR context or env vars are malformed.
    """
    if os.environ.get("GITHUB_ACTIONS") == "true":
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        if not repo:
            raise ConfigError("GITHUB_REPOSITORY environment variable is empty")

        ref = os.environ.get("GITHUB_REF", "")
        # Expected format: refs/pull/{number}/merge
        match = re.match(r"^refs/pull/(\d+)/merge$", ref)
        if not match:
            raise ConfigError(
                f"Not a pull_request event: GITHUB_REF={ref!r}. "
                "prbot must run on pull_request or pull_request_target events."
            )
        return {"repo": repo, "pr_number": match.group(1)}

    if os.environ.get("GITLAB_CI") == "true":
        repo = os.environ.get("CI_PROJECT_PATH", "")
        if not repo:
            raise ConfigError("CI_PROJECT_PATH environment variable is empty")

        mr_iid = os.environ.get("CI_MERGE_REQUEST_IID", "")
        if not mr_iid:
            raise ConfigError(
                "CI_MERGE_REQUEST_IID is not set. "
                "prbot must run on merge_request_event pipelines."
            )
        return {"repo": repo, "pr_number": mr_iid}

    raise ConfigError(
        "Not running in a recognized CI environment. "
        "Provide --repo and --pr flags explicitly."
    )


# --- TOML loading ---


def load_toml_config(
    config_path: str | None = None,
    env_vars: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Load configuration from TOML file.

    Search order:
    1. Explicit config_path if provided
    2. .prbot.toml in current directory  (non-CI only)
    3. pyproject.toml [tool.prbot] section  (non-CI only)

    SEC-CRED-03: the implicit search is refused in CI. A runner clones the
    branch under review into the job's working directory, so searching the
    current directory reads a file the reviewed code controls. That file can
    set api_base_url, which decides where the VCS token is sent, along with
    secret_name, the exclusion patterns and the suppression rules, so the
    code being reviewed would be choosing what its reviewer looks at and
    where its credentials go.

    An explicit --config path still works everywhere: naming the file is the
    operator's decision rather than the branch's.

    Returns empty dict if no config file found.
    """
    if env_vars is None:
        env_vars = dict(os.environ)

    paths_to_try: list[Path] = []

    if config_path:
        paths_to_try.append(Path(config_path))
    elif _in_ci(env_vars) or not env_vars.get("PRBOT_ALLOW_IMPLICIT_CONFIG"):
        # Refused by default rather than refused in CI (SEC-CRED-03). The
        # previous shape named three environment variables and fell through
        # to the unsafe branch when none was present, so any runner that
        # happened to set none of them got the implicit search back. Opting
        # in is for local use and never overrides the CI check, because
        # there the reviewed tree really is the working directory.
        logger.info(
            "Skipping the implicit .prbot.toml search: the working "
            "directory may hold the branch under review. Pass --config to "
            "name a file, or set PRBOT_ALLOW_IMPLICIT_CONFIG=1 locally.",
        )
        return {}
    else:
        paths_to_try.append(Path(".prbot.toml"))
        paths_to_try.append(Path("pyproject.toml"))

    for path in paths_to_try:
        if not path.is_file():
            continue

        try:
            with path.open("rb") as f:
                data = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"Invalid TOML in {path}: {e}") from e

        # If pyproject.toml, extract [tool.prbot] section
        if path.name == "pyproject.toml":
            tool_config = data.get("tool", {}).get("prbot", {})
            if tool_config:
                return dict(tool_config)
            continue

        # .prbot.toml — use [prbot] section or top-level
        if "prbot" in data:
            return dict(data["prbot"])
        return dict(data)

    return {}


# --- Config builder ---

_ENV_PREFIX = "PRBOT_"

# Fields that should be parsed as specific types from env vars
_INT_FIELDS = frozenset({
    "pr_number", "confidence_threshold", "blocker_threshold",
    "max_diff_tokens", "max_output_tokens", "timeout_seconds",
    "context_lines",
    "min_passing_score",
})
_FLOAT_FIELDS = frozenset({"budget_limit_usd"})
_BOOL_FIELDS = frozenset({"dry_run", "datamark_diff", "force_review"})
# Pydantic will not coerce a string to list[str], so without this the
# only way to set a list was a TOML file, and the container workflows
# pass configuration purely through env: (D6).
_LIST_FIELDS = frozenset({"excluded_patterns", "allowed_regions"})

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off", ""})


def _parse_bool(key: str, value: str) -> bool:
    """Parse a boolean env var, rejecting values it cannot interpret.

    Silently treating a typo as False hides the setting the caller asked
    for, which is how PRBOT_DRY_RUN went unnoticed (A1).
    """
    lowered = value.strip().lower()
    if lowered in _TRUE_VALUES:
        return True
    if lowered in _FALSE_VALUES:
        return False
    raise ConfigError(
        f"Invalid boolean for {key}: {value!r}. "
        f"Use one of {sorted(_TRUE_VALUES | _FALSE_VALUES - {''})}."
    )


def build_config(
    cli_args: dict[str, Any] | None = None,
    env_vars: dict[str, str] | None = None,
    toml_config: dict[str, Any] | None = None,
) -> PrBotConfig:
    """Build PrBotConfig with merge priority: CLI > env > TOML > defaults.

    Args:
        cli_args: Parsed CLI arguments (from argparse).
        env_vars: Override env var source (defaults to os.environ).
        toml_config: Override TOML config (defaults to load_toml_config()).
    """
    if env_vars is None:
        env_vars = dict(os.environ)
    if toml_config is None:
        config_path = (cli_args or {}).get("config")
        toml_config = load_toml_config(config_path, env_vars=env_vars)

    # Start with TOML values
    merged: dict[str, Any] = dict(toml_config)

    # Layer env vars (PRBOT_ prefix)
    for key, value in env_vars.items():
        if not key.startswith(_ENV_PREFIX):
            continue
        field_name = key[len(_ENV_PREFIX):].lower()
        if field_name in _INT_FIELDS:
            try:
                merged[field_name] = int(value)
            except ValueError as e:
                raise ConfigError(f"Invalid integer for {key}: {value!r}") from e
        elif field_name in _FLOAT_FIELDS:
            try:
                merged[field_name] = float(value)
            except ValueError as e:
                raise ConfigError(f"Invalid float for {key}: {value!r}") from e
        elif field_name in _BOOL_FIELDS:
            merged[field_name] = _parse_bool(key, value)
        elif field_name in _LIST_FIELDS:
            merged[field_name] = [
                item.strip() for item in value.split(",") if item.strip()
            ]
        else:
            merged[field_name] = value

    # Auto-detect platform if not set
    if "platform" not in merged:
        detected = detect_platform()
        if detected:
            merged["platform"] = detected

    # Auto-detect repo/pr from CI if not set
    if "repo" not in merged or "pr_number" not in merged:
        try:
            context = detect_pr_context()
            merged.setdefault("repo", context["repo"])
            if "pr_number" not in merged:
                merged["pr_number"] = int(context["pr_number"])
        except ConfigError:
            pass  # Will fail at validation if still missing

    # Layer CLI args (highest priority — overwrite everything)
    if cli_args:
        for key, value in cli_args.items():
            if value is not None and key != "config":
                merged[key] = value

    try:
        return PrBotConfig(**merged)
    except Exception as e:
        raise ConfigError(f"Configuration validation failed: {e}") from e
