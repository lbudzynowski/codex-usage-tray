"""Tests for narrow validated rate-limit models."""

import unittest

from codex_usage_tray.models import (
    RateLimitSnapshot,
    RateLimitValidationError,
    RateLimitWindow,
    deduplicate_snapshots,
    remaining_percentage,
)


class RemainingPercentageTests(unittest.TestCase):
    def test_converts_used_percentage_to_remaining_percentage(self) -> None:
        self.assertEqual(remaining_percentage(4), 96.0)
        self.assertEqual(remaining_percentage(25.5), 74.5)
        self.assertEqual(remaining_percentage(100), 0.0)

    def test_clamps_percentage_values_outside_the_valid_range(self) -> None:
        self.assertEqual(remaining_percentage(-10), 100.0)
        self.assertEqual(remaining_percentage(150), 0.0)

        below_zero = RateLimitWindow(-10, 300)
        above_one_hundred = RateLimitWindow(150, 300)
        self.assertEqual(below_zero.remaining_percent, 100.0)
        self.assertEqual(above_one_hundred.remaining_percent, 0.0)

    def test_rejects_non_finite_percentage_values(self) -> None:
        with self.assertRaises(RateLimitValidationError):
            remaining_percentage(float("nan"))
        with self.assertRaises(RateLimitValidationError):
            remaining_percentage(float("inf"))


class RateLimitSnapshotTests(unittest.TestCase):
    def test_handles_a_missing_primary_window(self) -> None:
        snapshot = RateLimitSnapshot.from_mapping(
            {
                "limitId": "codex",
                "secondary": {
                    "usedPercent": 20,
                    "windowDurationMins": 10_080,
                    "resetsAt": 1_784_889_359,
                },
            }
        )

        self.assertIsNone(snapshot.primary)
        self.assertIsNotNone(snapshot.secondary)
        self.assertEqual(snapshot.windows, (snapshot.secondary,))

    def test_handles_a_missing_secondary_window(self) -> None:
        snapshot = RateLimitSnapshot.from_mapping(
            {
                "limitId": "codex",
                "primary": {
                    "usedPercent": 4,
                    "windowDurationMins": 10_080,
                    "resetsAt": 1_784_889_359,
                },
            }
        )

        self.assertIsNotNone(snapshot.primary)
        self.assertIsNone(snapshot.secondary)
        self.assertEqual(snapshot.windows, (snapshot.primary,))

    def test_handles_an_empty_snapshot_as_a_safe_empty_state(self) -> None:
        snapshot = RateLimitSnapshot.from_mapping({"unrecognized": "ignored"})

        self.assertIsNone(snapshot.primary)
        self.assertIsNone(snapshot.secondary)
        self.assertEqual(snapshot.windows, ())

    def test_ignores_unknown_fields(self) -> None:
        snapshot = RateLimitSnapshot.from_mapping(
            {
                "limitId": "codex",
                "unknownTopLevel": {"private": "not retained"},
                "primary": {
                    "usedPercent": 4,
                    "windowDurationMins": 10_080,
                    "unknownWindowField": "ignored",
                },
            }
        )

        self.assertEqual(snapshot.limit_id, "codex")
        self.assertEqual(snapshot.primary, RateLimitWindow(4, 10_080))

    def test_rejects_a_partially_malformed_window(self) -> None:
        with self.assertRaisesRegex(
            RateLimitValidationError, "windowDurationMins is required"
        ):
            RateLimitSnapshot.from_mapping(
                {"primary": {"usedPercent": 4, "resetsAt": 1_784_889_359}}
            )

    def test_rejects_millisecond_reset_timestamps(self) -> None:
        with self.assertRaisesRegex(RateLimitValidationError, "Unix timestamp"):
            RateLimitWindow(
                used_percent=4,
                window_duration_mins=10_080,
                resets_at=1_784_889_359_000,
            )

    def test_deduplicates_identical_snapshots_and_preserves_order(self) -> None:
        first = RateLimitSnapshot.from_mapping(
            {
                "limitId": "codex",
                "primary": {
                    "usedPercent": 4,
                    "windowDurationMins": 10_080,
                    "resetsAt": 1_784_889_359,
                },
            }
        )
        duplicate = RateLimitSnapshot.from_mapping(
            {
                "limitId": "codex",
                "primary": {
                    "usedPercent": 4,
                    "windowDurationMins": 10_080,
                    "resetsAt": 1_784_889_359,
                },
                "ignored": "does not affect identity",
            }
        )
        distinct = RateLimitSnapshot.from_mapping(
            {
                "limitId": "another-limit",
                "primary": {
                    "usedPercent": 50,
                    "windowDurationMins": 300,
                },
            }
        )

        self.assertEqual(
            deduplicate_snapshots([first, duplicate, distinct]),
            (first, distinct),
        )


if __name__ == "__main__":
    unittest.main()
