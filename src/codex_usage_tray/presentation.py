"""Pure presentation values derived from validated rate-limit models."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import tzinfo
from math import isfinite

from .formatting import format_reset_timestamp, format_window_duration
from .models import RateLimitSnapshot, RateLimitWindow

EMPTY_LIMITS_MESSAGE = "No usage limits are available."
DEFAULT_LIMIT_TITLE = "Usage limits"


@dataclass(frozen=True, slots=True)
class PresentedRateLimitWindow:
    """Display-ready values for one duration-based quota window."""

    duration_label: str
    remaining_percent: float
    remaining_percent_text: str
    reset_time_text: str | None


@dataclass(frozen=True, slots=True)
class RateLimitPresentation:
    """Display-ready values for one selected rate-limit snapshot."""

    limit_id: str | None
    title: str
    windows: tuple[PresentedRateLimitWindow, ...]
    empty_message: str | None


def _validate_timezone(local_timezone: object) -> tzinfo:
    if not isinstance(local_timezone, tzinfo):
        raise TypeError("local_timezone must be a tzinfo instance")
    return local_timezone


def select_rate_limit_snapshot(
    snapshots: Iterable[RateLimitSnapshot],
) -> RateLimitSnapshot | None:
    """Prefer the Codex snapshot, otherwise return the first snapshot."""

    first: RateLimitSnapshot | None = None
    for snapshot in snapshots:
        if not isinstance(snapshot, RateLimitSnapshot):
            raise TypeError("snapshots must contain only RateLimitSnapshot values")
        if first is None:
            first = snapshot
        if snapshot.limit_id == "codex":
            return snapshot
    return first


def format_remaining_percent(value: int | float) -> str:
    """Clamp and format a remaining percentage without redundant decimals."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("remaining percentage must be a finite number")

    number = float(value)
    if not isfinite(number):
        raise ValueError("remaining percentage must be a finite number")

    clamped = max(0.0, min(100.0, number))
    if clamped.is_integer():
        return f"{int(clamped)}%"
    return f"{clamped:g}%"


def present_rate_limit_window(
    window: RateLimitWindow,
    local_timezone: tzinfo,
) -> PresentedRateLimitWindow:
    """Convert one validated window to immutable display values."""

    local_timezone = _validate_timezone(local_timezone)
    if not isinstance(window, RateLimitWindow):
        raise TypeError("window must be a RateLimitWindow")

    remaining_percent = window.remaining_percent
    reset_time_text = (
        None
        if window.resets_at is None
        else format_reset_timestamp(window.resets_at, local_timezone)
    )
    return PresentedRateLimitWindow(
        duration_label=format_window_duration(window.window_duration_mins),
        remaining_percent=remaining_percent,
        remaining_percent_text=format_remaining_percent(remaining_percent),
        reset_time_text=reset_time_text,
    )


def present_rate_limits(
    snapshots: Iterable[RateLimitSnapshot],
    local_timezone: tzinfo,
) -> RateLimitPresentation:
    """Select and convert one snapshot without relying on window positions."""

    local_timezone = _validate_timezone(local_timezone)
    snapshot = select_rate_limit_snapshot(snapshots)
    if snapshot is None:
        return RateLimitPresentation(
            limit_id=None,
            title=DEFAULT_LIMIT_TITLE,
            windows=(),
            empty_message=EMPTY_LIMITS_MESSAGE,
        )

    windows = tuple(
        present_rate_limit_window(window, local_timezone)
        for window in snapshot.windows
    )
    return RateLimitPresentation(
        limit_id=snapshot.limit_id,
        title=snapshot.limit_name or snapshot.limit_id or DEFAULT_LIMIT_TITLE,
        windows=windows,
        empty_message=None if windows else EMPTY_LIMITS_MESSAGE,
    )
