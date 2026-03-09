"""Data residency validation (story-8-3).

Validates AWS region against allowed_regions config and
geographic inference profile routing.
"""

from __future__ import annotations

import structlog

from prbot.exceptions import ConfigError

logger = structlog.get_logger()

# US-prefixed inference profiles require US regions
_US_REGIONS = frozenset({
    "us-east-1", "us-east-2", "us-west-1", "us-west-2",
})

_EU_REGIONS = frozenset({
    "eu-west-1", "eu-west-2", "eu-west-3",
    "eu-central-1", "eu-central-2",
    "eu-north-1", "eu-south-1", "eu-south-2",
})

_AP_REGIONS = frozenset({
    "ap-southeast-1", "ap-southeast-2", "ap-southeast-3",
    "ap-northeast-1", "ap-northeast-2", "ap-northeast-3",
    "ap-south-1", "ap-south-2", "ap-east-1",
})


def _get_profile_region_group(model_id: str) -> str | None:
    """Extract geographic prefix from inference profile model ID.

    e.g. 'us.anthropic.claude-sonnet-4-20250514' → 'us'
    """
    if "." in model_id:
        prefix = model_id.split(".")[0]
        if prefix in ("us", "eu", "ap"):
            return prefix
    return None


def _region_in_group(region: str, group: str) -> bool:
    """Check if a region belongs to a geographic group."""
    groups = {"us": _US_REGIONS, "eu": _EU_REGIONS, "ap": _AP_REGIONS}
    return region in groups.get(group, set())


def validate_data_residency(
    aws_region: str,
    allowed_regions: list[str],
    model_ids: list[str] | None = None,
) -> None:
    """Validate data residency constraints.

    Raises ConfigError if:
    - aws_region not in allowed_regions (when allowed_regions non-empty)
    - Model uses geographic prefix but region doesn't match
    """
    # Empty allowed_regions means no restrictions
    if allowed_regions and aws_region not in allowed_regions:
        raise ConfigError(
            f"AWS region {aws_region!r} not in allowed regions: "
            f"{allowed_regions}"
        )

    # Validate geographic inference profile routing
    if model_ids:
        for model_id in model_ids:
            group = _get_profile_region_group(model_id)
            if group and not _region_in_group(aws_region, group):
                raise ConfigError(
                    f"Model {model_id!r} uses {group.upper()} "
                    f"inference profile but region "
                    f"{aws_region!r} is not in {group.upper()} "
                    f"region group"
                )


def log_data_flow(
    source_platform: str,
    processing_region: str,
    output_destination: str,
) -> None:
    """Log data flow path for audit."""
    logger.info(
        "data_flow.path",
        source_platform=source_platform,
        processing_region=processing_region,
        output_destination=output_destination,
    )
