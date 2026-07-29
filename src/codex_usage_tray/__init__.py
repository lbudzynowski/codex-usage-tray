"""Core data types and formatting helpers for Codex Usage Tray."""

__version__ = "0.2.0"

from .formatting import (
    format_reset_timestamp,
    format_window_duration,
    unix_timestamp_to_local_datetime,
)
from .models import (
    RateLimitSnapshot,
    RateLimitValidationError,
    RateLimitWindow,
    deduplicate_snapshots,
    remaining_percentage,
)

__all__ = [
    "__version__",
    "RateLimitSnapshot",
    "RateLimitValidationError",
    "RateLimitWindow",
    "deduplicate_snapshots",
    "format_reset_timestamp",
    "format_window_duration",
    "remaining_percentage",
    "unix_timestamp_to_local_datetime",
]
