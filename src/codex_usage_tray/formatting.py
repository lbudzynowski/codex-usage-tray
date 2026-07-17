"""Presentation helpers for rate-limit durations and reset times."""

from datetime import UTC, datetime, tzinfo

from .models import MAX_UNIX_TIMESTAMP_SECONDS, RateLimitValidationError


def _positive_minutes(minutes: object) -> int:
    if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes <= 0:
        raise RateLimitValidationError("window duration must be a positive integer")
    return minutes


def format_window_duration(minutes: int) -> str:
    """Format a quota window length derived only from its duration in minutes."""

    remaining = _positive_minutes(minutes)
    parts: list[str] = []

    for unit_minutes, singular in (
        (24 * 60, "day"),
        (60, "hour"),
        (1, "minute"),
    ):
        count, remaining = divmod(remaining, unit_minutes)
        if count:
            suffix = singular if count == 1 else f"{singular}s"
            parts.append(f"{count} {suffix}")

    return " ".join(parts)


def unix_timestamp_to_local_datetime(
    timestamp: int, local_timezone: tzinfo | None = None
) -> datetime:
    """Convert a Unix-seconds timestamp to a timezone-aware local datetime."""

    if (
        isinstance(timestamp, bool)
        or not isinstance(timestamp, int)
        or timestamp < 0
        or timestamp > MAX_UNIX_TIMESTAMP_SECONDS
    ):
        raise RateLimitValidationError(
            "reset timestamp must be a Unix timestamp in seconds"
        )

    try:
        utc_datetime = datetime.fromtimestamp(timestamp, tz=UTC)
        return utc_datetime.astimezone(local_timezone)
    except (OverflowError, OSError, ValueError) as error:
        raise RateLimitValidationError(
            "reset timestamp cannot be represented as a datetime"
        ) from error


def format_reset_timestamp(
    timestamp: int, local_timezone: tzinfo | None = None
) -> str:
    """Format a Unix reset timestamp in the requested or system local timezone."""

    local_datetime = unix_timestamp_to_local_datetime(timestamp, local_timezone)
    return local_datetime.strftime("%Y-%m-%d %H:%M:%S %Z")
