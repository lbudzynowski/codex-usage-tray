"""Validated rate-limit models independent of transport and presentation."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from math import isfinite
from typing import Self

MAX_UNIX_TIMESTAMP_SECONDS = 253_402_300_799
_MISSING = object()


class RateLimitValidationError(ValueError):
    """Raised when rate-limit data cannot be represented safely."""


def _finite_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RateLimitValidationError(f"{field_name} must be a finite number")

    number = float(value)
    if not isfinite(number):
        raise RateLimitValidationError(f"{field_name} must be a finite number")
    return number


def _positive_integer(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RateLimitValidationError(f"{field_name} must be a positive integer")
    return value


def _optional_unix_timestamp(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > MAX_UNIX_TIMESTAMP_SECONDS
    ):
        raise RateLimitValidationError(
            f"{field_name} must be a Unix timestamp in seconds"
        )
    return value


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise RateLimitValidationError(f"{field_name} must be a non-empty string")
    return value


def remaining_percentage(used_percent: int | float) -> float:
    """Convert percentage used to percentage remaining and clamp it safely."""

    used = _finite_number(used_percent, "usedPercent")
    return max(0.0, min(100.0, 100.0 - used))


@dataclass(frozen=True, slots=True)
class RateLimitWindow:
    """One quota window without assumptions about its semantic position."""

    used_percent: float
    window_duration_mins: int
    resets_at: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "used_percent", _finite_number(self.used_percent, "usedPercent")
        )
        object.__setattr__(
            self,
            "window_duration_mins",
            _positive_integer(self.window_duration_mins, "windowDurationMins"),
        )
        object.__setattr__(
            self,
            "resets_at",
            _optional_unix_timestamp(self.resets_at, "resetsAt"),
        )

    @property
    def remaining_percent(self) -> float:
        """Return the remaining percentage, clamped to the inclusive 0-100 range."""

        return remaining_percentage(self.used_percent)

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> Self:
        """Build a window from required known fields while ignoring unknown fields."""

        if not isinstance(data, Mapping):
            raise RateLimitValidationError("rate-limit window must be a mapping")

        used_percent = data.get("usedPercent", _MISSING)
        if used_percent is _MISSING:
            raise RateLimitValidationError("usedPercent is required")

        window_duration_mins = data.get("windowDurationMins", _MISSING)
        if window_duration_mins is _MISSING:
            raise RateLimitValidationError("windowDurationMins is required")

        return cls(
            used_percent=_finite_number(used_percent, "usedPercent"),
            window_duration_mins=_positive_integer(
                window_duration_mins, "windowDurationMins"
            ),
            resets_at=_optional_unix_timestamp(data.get("resetsAt"), "resetsAt"),
        )


def _optional_window(value: object, field_name: str) -> RateLimitWindow | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise RateLimitValidationError(f"{field_name} must be a mapping or null")
    return RateLimitWindow.from_mapping(value)


@dataclass(frozen=True, slots=True)
class RateLimitSnapshot:
    """A narrow set of rate-limit windows needed by the application."""

    limit_id: str | None = None
    limit_name: str | None = None
    primary: RateLimitWindow | None = None
    secondary: RateLimitWindow | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "limit_id", _optional_text(self.limit_id, "limitId"))
        object.__setattr__(
            self, "limit_name", _optional_text(self.limit_name, "limitName")
        )
        if self.primary is not None and not isinstance(self.primary, RateLimitWindow):
            raise RateLimitValidationError("primary must be a RateLimitWindow or null")
        if self.secondary is not None and not isinstance(
            self.secondary, RateLimitWindow
        ):
            raise RateLimitValidationError("secondary must be a RateLimitWindow or null")

    @property
    def windows(self) -> tuple[RateLimitWindow, ...]:
        """Return only the windows that are present, preserving source order."""

        return tuple(
            window
            for window in (self.primary, self.secondary)
            if window is not None
        )

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> Self:
        """Build a snapshot from known fields while safely ignoring unknown fields."""

        if not isinstance(data, Mapping):
            raise RateLimitValidationError("rate-limit snapshot must be a mapping")

        return cls(
            limit_id=_optional_text(data.get("limitId"), "limitId"),
            limit_name=_optional_text(data.get("limitName"), "limitName"),
            primary=_optional_window(data.get("primary"), "primary"),
            secondary=_optional_window(data.get("secondary"), "secondary"),
        )


def deduplicate_snapshots(
    snapshots: Iterable[RateLimitSnapshot],
) -> tuple[RateLimitSnapshot, ...]:
    """Remove identical snapshots while preserving the first occurrence."""

    unique: list[RateLimitSnapshot] = []
    seen: set[RateLimitSnapshot] = set()
    for snapshot in snapshots:
        if not isinstance(snapshot, RateLimitSnapshot):
            raise RateLimitValidationError(
                "snapshots must contain only RateLimitSnapshot values"
            )
        if snapshot not in seen:
            seen.add(snapshot)
            unique.append(snapshot)
    return tuple(unique)
