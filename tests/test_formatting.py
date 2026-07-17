"""Tests for duration and reset-time formatting."""

import unittest
from zoneinfo import ZoneInfo

from codex_usage_tray.formatting import (
    format_reset_timestamp,
    format_window_duration,
    unix_timestamp_to_local_datetime,
)
from codex_usage_tray.models import RateLimitValidationError


class WindowDurationFormattingTests(unittest.TestCase):
    def test_formats_window_durations_from_minutes(self) -> None:
        expected_labels = {
            1: "1 minute",
            30: "30 minutes",
            60: "1 hour",
            90: "1 hour 30 minutes",
            300: "5 hours",
            1_440: "1 day",
            10_080: "7 days",
            1_590: "1 day 2 hours 30 minutes",
        }

        for minutes, expected in expected_labels.items():
            with self.subTest(minutes=minutes):
                self.assertEqual(format_window_duration(minutes), expected)

    def test_rejects_non_positive_or_malformed_durations(self) -> None:
        for value in (0, -1, True):
            with self.subTest(value=value):
                with self.assertRaises(RateLimitValidationError):
                    format_window_duration(value)


class ResetTimestampFormattingTests(unittest.TestCase):
    def test_formats_unix_seconds_in_the_selected_local_timezone(self) -> None:
        warsaw = ZoneInfo("Europe/Warsaw")

        self.assertEqual(
            format_reset_timestamp(1_784_889_359, warsaw),
            "2026-07-24 12:35:59 CEST",
        )

    def test_returns_a_timezone_aware_datetime(self) -> None:
        warsaw = ZoneInfo("Europe/Warsaw")
        converted = unix_timestamp_to_local_datetime(1_784_889_359, warsaw)

        self.assertIsNotNone(converted.tzinfo)
        self.assertEqual(converted.utcoffset(), warsaw.utcoffset(converted))
        self.assertEqual(converted.timestamp(), 1_784_889_359)

    def test_rejects_millisecond_timestamps_instead_of_rescaling_them(self) -> None:
        with self.assertRaisesRegex(RateLimitValidationError, "Unix timestamp"):
            format_reset_timestamp(1_784_889_359_000, ZoneInfo("Europe/Warsaw"))


if __name__ == "__main__":
    unittest.main()
